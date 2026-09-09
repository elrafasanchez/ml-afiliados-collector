"""Comprobaciones estructurales del flujo de trabajo de GitHub Actions.

Un archivo de Actions solo se valida al subirlo: si no parsea, GitHub crea una
ejecución fallida sin pasos y el error queda dentro de su interfaz. Estas
pruebas atrapan aquí los fallos que impiden que el archivo arranque siquiera.

Ocurrió: el cuerpo de un mensaje se escribió en varias líneas dentro de unas
comillas, y esas líneas quedaron en la columna 0. Un bloque literal de YAML
(`run: |`) termina en cuanto la sangría baja del nivel del bloque, así que YAML
intentó leer ese texto como una clave nueva y el archivo dejó de ser válido.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "sync.yml"

_BLOCK_START = re.compile(r"^(\s*)(?:-\s+)?(run|body|script):\s*[|>][-+]?\s*$")

#: Una línea que legítimamente termina un bloque literal: otra clave, un
#: elemento de lista o un comentario. Cualquier otra cosa es texto suelto que
#: se salió del bloque.
_YAML_STRUCTURE = re.compile(r'^\s*(?:#|-\s|[\w.\-\"\']+\s*:)')


def indentation(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


class BlockScalarTest(unittest.TestCase):
    """Ninguna línea de un bloque literal puede salirse de su sangría.

    La comprobación no puede limitarse a "la sangría bajó, luego el bloque
    terminó": eso es precisamente lo que hace YAML, y es lo que convierte el
    error en un archivo inválido. Lo que se exige es que la línea donde el
    bloque termina **sea** estructura de YAML —otra clave, un elemento de lista
    o un comentario— y no prosa que se escapó del bloque.
    """

    def test_block_scalars_stay_indented(self):
        lines = WORKFLOW.read_text().splitlines()
        problems: list[str] = []

        index = 0
        while index < len(lines):
            match = _BLOCK_START.match(lines[index])
            if not match:
                index += 1
                continue

            key_indent = len(match.group(1))
            index += 1

            # La sangría del bloque la fija su primera línea con contenido.
            block_indent = None
            while index < len(lines):
                line = lines[index]
                if not line.strip():
                    index += 1
                    continue
                current = indentation(line)

                if block_indent is None:
                    if current <= key_indent:
                        break
                    block_indent = current
                    index += 1
                    continue

                if current >= block_indent:
                    index += 1
                    continue

                # El bloque termina aquí. Solo es válido si lo que sigue es
                # estructura de YAML.
                if not _YAML_STRUCTURE.match(line):
                    problems.append(
                        f"línea {index + 1}: sangría {current}, por debajo del "
                        f"bloque ({block_indent}), y no es una clave YAML: "
                        f"{line.strip()[:60]!r}"
                    )
                break

        self.assertEqual(
            problems,
            [],
            "Una línea sin sangrar cierra el bloque y rompe el archivo:\n"
            + "\n".join(problems),
        )


class StructureTest(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()
        self.lines = self.text.splitlines()

    def test_has_the_keys_actions_requires(self):
        for key in ("name:", "on:", "jobs:"):
            self.assertTrue(
                any(line.startswith(key) for line in self.lines),
                f"Falta la clave de nivel superior {key}",
            )

    def test_uses_no_tabs(self):
        # YAML prohíbe el tabulador como sangría, y es invisible al leer.
        offenders = [i + 1 for i, line in enumerate(self.lines) if "\t" in line]
        self.assertEqual(offenders, [], f"Tabuladores en las líneas {offenders}")

    def test_indentation_is_always_even(self):
        odd = [
            i + 1
            for i, line in enumerate(self.lines)
            if line.strip() and not line.lstrip().startswith("#") and indentation(line) % 2
        ]
        self.assertEqual(odd, [], f"Sangría impar en las líneas {odd}")

    def test_every_secret_it_reads_is_documented(self):
        # Un secreto que el flujo lee pero nadie configuró produce un fallo
        # silencioso: la variable llega vacía y el trabajo falla más adelante.
        usados = set(re.findall(r"secrets\.([A-Z_]+)", self.text))
        readme = (WORKFLOW.resolve().parents[2] / "README.md").read_text()
        for secreto in usados:
            self.assertIn(
                secreto, readme, f"El flujo usa {secreto} pero el README no lo explica"
            )

    def test_schedules_match_the_cases_that_handle_them(self):
        # Si un cron cambia y el `case` no, ese horario caería en el trabajo por
        # omisión sin que nada avise.
        crons = set(re.findall(r"- cron: '([^']+)'", self.text))
        manejados = set(re.findall(r"^\s*'([^']+)'\)\s+job=", self.text, re.M))
        self.assertTrue(
            manejados <= crons,
            f"El `case` atiende horarios que ya no existen: {manejados - crons}",
        )


if __name__ == "__main__":
    unittest.main()
