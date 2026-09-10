import json
import unittest
from buzz_cards import ChatReply, card_tags


class Cards(unittest.TestCase):
    def test_text_fallback_and_inert_metadata(self):
        reply=ChatReply('Original command-compatible reply',kind='proposal',title='Decision',
                        document_id='doc',revision='revision',state='proposed',sources=['a']*10)
        self.assertEqual(str(reply),'Original command-compatible reply')
        data=json.loads(card_tags(reply,'channel')[0][1])
        self.assertEqual(data['version'],1)
        self.assertEqual(data['channel_id'],'channel')
        self.assertEqual(len(data['sources']),6)
        self.assertNotIn('actions',data)
        self.assertNotIn('url',data)

    def test_plain_responses_remain_plain(self):
        self.assertEqual(card_tags('Unchanged reply','channel'),[])
