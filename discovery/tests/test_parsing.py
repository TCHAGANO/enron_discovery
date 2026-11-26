from email import message_from_bytes

from django.test import SimpleTestCase

from discovery.parsing import clean_body, is_reply_subject, normalize_subject, parse_addresses, parse_email_date, parse_message

RAW = b"""Message-ID: <123.JavaMail.evans@thyme>
Date: Mon, 14 May 2001 16:39:00 -0700 (PDT)
From: phillip.allen@enron.com
To: tim.belden@enron.com, "John Doe" <john.doe@enron.com>
Cc: TIM.BELDEN@enron.com
Subject: RE: Fw: Forecast
X-From: Phillip K Allen
Content-Type: text/plain; charset=us-ascii

Here is our forecast.

 -----Original Message-----
From: Tim
Old content
"""


class ParsingTests(SimpleTestCase):
    def test_normalize_subject(self):
        self.assertEqual(normalize_subject("RE: Fw:  Forecast  Q1"), "forecast q1")
        self.assertEqual(normalize_subject("Re[2]: FWD: Hello"), "hello")
        self.assertEqual(normalize_subject("Reorganisation"), "reorganisation")
        self.assertEqual(normalize_subject(""), "")

    def test_is_reply_subject(self):
        self.assertTrue(is_reply_subject("RE: x"))
        self.assertTrue(is_reply_subject("Fw: x"))
        self.assertFalse(is_reply_subject("Reorg"))
        self.assertFalse(is_reply_subject(""))

    def test_parse_addresses_dedup_and_lowercase(self):
        self.assertEqual(
            parse_addresses("A@Enron.com, 'B' <b@enron.com>", "a@enron.com, not-an-address"),
            [("a@enron.com", ""), ("b@enron.com", "B")],
        )

    def test_parse_email_date_rejects_out_of_range(self):
        self.assertEqual(parse_email_date("Mon, 14 May 2001 16:39:00 -0700").year, 2001)
        self.assertIsNone(parse_email_date("Mon, 14 May 2044 16:39:00 -0700"))
        self.assertIsNone(parse_email_date("n'importe quoi"))
        self.assertIsNone(parse_email_date(""))

    def test_clean_body_strips_quoted_message(self):
        body = "Bonjour\n\n -----Original Message-----\nFrom: x\nancien"
        self.assertEqual(clean_body(body), "Bonjour")

    def test_clean_body_keeps_forwarded_content(self):
        body = "FYI\n---------------------- Forwarded by A on 01/01/2001 ---------------------------\nContenu"
        self.assertIn("Contenu", clean_body(body))

    def test_parse_message(self):
        parsed = parse_message(message_from_bytes(RAW), "<fallback>")
        self.assertEqual(parsed.message_id, "<123.JavaMail.evans@thyme>")
        self.assertEqual(parsed.normalized_subject, "forecast")
        self.assertEqual(parsed.sender, ("phillip.allen@enron.com", "Phillip K Allen"))
        self.assertEqual([a for a, _ in parsed.to], ["tim.belden@enron.com", "john.doe@enron.com"])
        self.assertEqual(parsed.body, "Here is our forecast.")
        self.assertFalse(parsed.date_was_invalid)
