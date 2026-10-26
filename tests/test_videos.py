import unittest
from datetime import datetime,timezone
from unittest.mock import patch
from mlaf.videos import collect_videos,normalize_video,cents
from mlaf.errors import SchemaError
C={'id_campaign':'campaign','title_reward':'Shark','status':'ENROLLED','end_date':'2026-10-28'}
V={'id':'video','views':23924,'earnings':454.10,'last_sync_time':'2026-10-19 16:04:49','linked_date':'2026-10-17 20:16:32','monetization':True}
class VideosTest(unittest.TestCase):
 def test_authoritative_amount_and_dates(self):
  v=normalize_video(C,V,30)
  self.assertEqual(v['earningsCents'],45410)
  self.assertEqual(v['monetizationEnd'],'2026-11-16')
  self.assertEqual(v['sourceAt'],'2026-10-19T16:04:49Z')
 def test_hermosillo_month_boundary(self):
  self.assertEqual(normalize_video(C,{**V,'last_sync_time':'2026-11-01 02:00:00'},30)['sourceMonth'],'2026-10')
 def test_event_campaign_without_linked_product_date(self):
  v=normalize_video(C,{**V,'linked_date':None,'create_time':1792877826000,'last_sync_time':'2026-10-26 15:10:08','linked_products':[],'earnings':43.7},30)
  self.assertEqual(v['linkedDate'],'2026-10-24')
  self.assertEqual(v['monetizationEnd'],'2026-11-23')
  self.assertEqual(v['earningsCents'],4370)
 def test_no_date_must_not_invent_start(self):
  with self.assertRaises(SchemaError):normalize_video(C,{**V,'linked_date':None},30)
 def test_linked_date_takes_precedence(self):
  self.assertEqual(normalize_video(C,{**V,'create_time':1792877826000},30)['linkedDate'],'2026-10-17')
 def test_invalid_amount_never_zero(self):
  for value in [None,'NaN',-1]:
   with self.assertRaises(SchemaError):cents(value)
 def test_closed_campaign_still_tracked(self):
  class Source:
   def _request(self,*a,**kw):return ''
  with patch('mlaf.videos.extract_page_props',side_effect=[{'influencerCampaignsData':[{**C,'status':'ENDED','postulation':{'status':'active'}}]},{'videos':{'linked_media':[V],'total_items':1},'monetizeElapsedDays':30}]):
   result=collect_videos(Source(),datetime(2026,11,2,tzinfo=timezone.utc))
  self.assertEqual(len(result['videos']),1)
 def test_incomplete_catalog_fails(self):
  class Source:
   def _request(self,*a,**kw):return ''
  with patch('mlaf.videos.extract_page_props',return_value={}):
   with self.assertRaises(SchemaError):collect_videos(Source())
if __name__=='__main__':unittest.main()
