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

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
#: Todos los flujos del repositorio, no solo uno. Una comprobación que solo mira
#: un archivo deja de proteger en cuanto se añade el siguiente.
WORKFLOWS = sorted(WORKFLOWS_DIR.glob("*.yml"))
WORKFLOW = WORKFLOWS_DIR / "sync.yml"

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
        for workflow in WORKFLOWS:
            with self.subTest(workflow=workflow.name):
                self.assertEqual(self._offenders(workflow), [], f"{workflow.name}: bloque roto")

    @staticmethod
    def _offenders(workflow: Path) -> list[str]:
        lines = workflow.read_text().splitlines()
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

        return problems


class StructureTest(unittest.TestCase):
    def test_every_workflow_has_the_keys_actions_requires(self):
        for workflow in WORKFLOWS:
            lines = workflow.read_text().splitlines()
            for key in ("name:", "on:", "jobs:"):
                with self.subTest(workflow=workflow.name, key=key):
                    self.assertTrue(
                        any(line.startswith(key) for line in lines),
                        f"{workflow.name}: falta la clave {key}",
                    )

    def test_no_workflow_uses_tabs(self):
        # YAML prohíbe el tabulador como sangría, y es invisible al leer.
        for workflow in WORKFLOWS:
            lines = workflow.read_text().splitlines()
            offenders = [i + 1 for i, line in enumerate(lines) if "\t" in line]
            with self.subTest(workflow=workflow.name):
                self.assertEqual(offenders, [], f"{workflow.name}: tabuladores en {offenders}")

    def test_indentation_is_always_even(self):
        for workflow in WORKFLOWS:
            lines = workflow.read_text().splitlines()
            odd = [
                i + 1
                for i, line in enumerate(lines)
                if line.strip() and not line.lstrip().startswith("#") and indentation(line) % 2
            ]
            with self.subTest(workflow=workflow.name):
                self.assertEqual(odd, [], f"{workflow.name}: sangría impar en {odd}")

    def test_the_two_businesses_never_share_a_concurrency_group(self):
        # Un grupo compartido haría que una corrida de Dental Amigo esperara o
        # cancelara a una de Mercado Libre, que está congelado y no debe verse
        # afectado desde aquí.
        grupos = []
        for workflow in WORKFLOWS:
            match = re.search(r"^concurrency:\n\s+group:\s*(\S+)", workflow.read_text(), re.M)
            if match:
                grupos.append(match.group(1))
        self.assertEqual(len(grupos), len(set(grupos)), f"Grupos repetidos: {grupos}")

    def test_every_secret_any_workflow_reads_is_documented(self):
        # Un secreto que un flujo lee pero nadie configuró produce un fallo
        # silencioso: la variable llega vacía y el trabajo falla más adelante.
        readme = (WORKFLOWS_DIR.parents[1] / "README.md").read_text()
        for workflow in WORKFLOWS:
            for secreto in sorted(set(re.findall(r"secrets\.([A-Z_]+)", workflow.read_text()))):
                with self.subTest(workflow=workflow.name, secret=secreto):
                    self.assertIn(
                        secreto, readme,
                        f"{workflow.name} usa {secreto} pero el README no lo explica",
                    )

    def test_schedules_match_the_cases_that_handle_them(self):
        # Si un cron cambia y el `case` no, ese horario caería en el trabajo por
        # omisión sin que nada avise.
        for workflow in WORKFLOWS:
            text = workflow.read_text()
            crons = set(re.findall(r"- cron: '([^']+)'", text))
            manejados = set(re.findall(r"^\s*'([^']+)'\)\s+job=", text, re.M))
            with self.subTest(workflow=workflow.name):
                self.assertTrue(
                    manejados <= crons,
                    f"{workflow.name}: el `case` atiende horarios inexistentes: {manejados - crons}",
                )


if __name__ == "__main__":
    unittest.main()
