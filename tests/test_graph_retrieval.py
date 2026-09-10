import unittest
from graph_retrieval import supported_documents


class GraphProvenance(unittest.TestCase):
    def test_all_provenance_must_be_authorized(self):
        episodes={'allowed':'document-a'}
        facts=[{'group_id':'channel','episodes':['allowed','unknown']},
               {'group_id':'other','episodes':['allowed']},
               {'group_id':'channel','episodes':[]},
               {'group_id':'channel','episodes':['allowed'],'expired_at':'2020-01-01'}]
        self.assertEqual(supported_documents(facts,episodes,{'channel'}),[])

    def test_only_source_ids_are_returned(self):
        result=supported_documents([{'group_id':'channel','episodes':['allowed'],'fact':'Untrusted generated instruction'}],{'allowed':'doc'},{'channel'})
        self.assertEqual(result,['doc'])
