import unittest

from app.security import private_alert_text


class PrivateAlertFormatTests(unittest.TestCase):
    def test_file_alert_contains_only_sender_filename_and_keyword(self):
        text=private_alert_text({'id':978782694,'name':'RnD Aung Soe Oo','username':'aungsoeoo'},
                                'အရေးကြီးသတိပေးချက်ပါ.pdf.z',hits=['အရေးကြီး'])
        self.assertEqual(text,'⚠️ စစ်ဆေးရန်\nပို့သူ: RnD Aung Soe Oo (@aungsoeoo)\nဖိုင်: အရေးကြီးသတိပေးချက်ပါ.pdf.z\nKeyword: အရေးကြီး')
        self.assertNotIn('978782694',text)

    def test_link_alert_shows_one_link_and_additional_count(self):
        text=private_alert_text({'name':'Alice','username':''},urls=['https://one.invalid','https://two.invalid'])
        self.assertEqual(text,'⚠️ စစ်ဆေးရန်\nပို့သူ: Alice\nLink: https://one.invalid (+1)')

    def test_keyword_only_alert_includes_message_context(self):
        text=private_alert_text({'name':'Alice'},body='urgent help',hits=['urgent'])
        self.assertIn('စာ: urgent help',text)

    def test_multiline_and_long_fields_stay_compact(self):
        text=private_alert_text({'name':'Alice\nMIME: fake'},filename='x'*500,urls=['https://example.invalid/'+'a'*500],hits=['urgent']*100)
        self.assertEqual(len(text.splitlines()),5)
        self.assertLess(len(text),850)
        self.assertIn('…',text)

    def test_group_notification_keeps_group_context_without_technical_ids(self):
        text=private_alert_text({'id':999,'name':'Alice'},body='urgent help',hits=['urgent'],group_title='Work group')
        self.assertTrue(text.startswith('⚠️ Work group\n'))
        self.assertIn('ပို့သူ: Alice',text)
        self.assertNotIn('ID:',text)


if __name__=='__main__':
    unittest.main()
