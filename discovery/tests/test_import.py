import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from discovery.models import Attachment, Email, Employee, Folder, Thread


def _write(root, rel, content):
    path = Path(root, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="latin-1")


def _mail(mid, date, sender, to, subject, body, extra=""):
    return (
        f"Message-ID: {mid}\nDate: {date}\nFrom: {sender}\nTo: {to}\nSubject: {subject}\n{extra}"
        f"Content-Type: text/plain; charset=us-ascii\n\n{body}\n"
    )


MULTIPART = """Message-ID: <att@x>
Date: Tue, 15 May 2001 10:00:00 -0700
From: kenneth.lay@enron.com
To: jeff.skilling@enron.com
Subject: Rapport
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="XX"

--XX
Content-Type: text/plain

Voir piece jointe.
--XX
Content-Type: application/pdf
Content-Disposition: attachment; filename="rapport.pdf"

JVBERi0xLjQK
--XX--
"""


class ImportCommandTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        _write(root, "lay-k/sent/1.", _mail("<a@x>", "Mon, 14 May 2001 09:00:00 -0700", "kenneth.lay@enron.com",
                                            "jeff.skilling@enron.com", "Raptor", "Point sur Raptor."))
        _write(root, "skilling-j/inbox/1.", _mail("<a-copy@x>", "Mon, 14 May 2001 09:00:00 -0700",
                                                  "kenneth.lay@enron.com", "jeff.skilling@enron.com", "Raptor",
                                                  "Point sur Raptor."))
        _write(root, "skilling-j/sent/1.", _mail("<b@x>", "Mon, 14 May 2001 11:00:00 -0700", "jeff.skilling@enron.com",
                                                 "kenneth.lay@enron.com", "RE: Raptor", "D'accord."))
        # reponse liee par en-tete, importee AVANT son parent (ordre alphabetique)
        _write(root, "aaa-a/inbox/1.", _mail("<c@x>", "Mon, 14 May 2001 12:00:00 -0700", "andrew.fastow@enron.com",
                                             "jeff.skilling@enron.com", "Autre sujet", "Vu.", "In-Reply-To: <b@x>\n"))
        # meme sujet mais autres personnes : autre conversation
        _write(root, "lay-k/inbox/2.", _mail("<d@x>", "Tue, 15 May 2001 09:00:00 -0700", "sherron.watkins@enron.com",
                                             "vince.kaminski@enron.com", "RE: Raptor", "Autre fil."))
        # lettre d'information : meme sujet, memes personnes, sans RE: -> pas de thread
        for i in (5, 6):
            _write(root, f"lay-k/inbox/{i}.", _mail(f"<news{i}@x>", f"Wed, {i + 11} May 2001 08:00:00 -0700",
                                                   "news@enron.com", "kenneth.lay@enron.com", "Enron Mentions", "..."))
        _write(root, "lay-k/inbox/3.", MULTIPART)
        _write(root, "lay-k/inbox/4.", "From: x@enron.com\nSubject: sans id\n\ncorps")

    def tearDown(self):
        self.tmp.cleanup()

    def _import(self):
        out = StringIO()
        call_command("import_enron", path=self.tmp.name, batch_size=2, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_import_creates_everything(self):
        self._import()
        self.assertEqual(Email.objects.count(), 9)
        self.assertTrue(Folder.objects.filter(path="lay-k/inbox", mailbox="lay-k", name="inbox").exists())
        self.assertEqual(Folder.objects.filter(name="inbox").count(), 3)
        self.assertEqual(Employee.objects.get(email="kenneth.lay@enron.com").sent_emails.count(), 3)
        self.assertEqual(Attachment.objects.get().filename, "rapport.pdf")
        self.assertEqual(Email.objects.get(message_id="<att@x>").body, "Voir piece jointe.")
        self.assertFalse(Email.objects.filter(search_vector=None).exists())
        self.assertTrue(Email.objects.filter(message_id="<file:lay-k/inbox/4.>").exists())

    def test_import_is_idempotent(self):
        self._import()
        out = self._import()
        self.assertEqual(Email.objects.count(), 9)
        self.assertIn("importes=0", out)

    def test_threads_are_rebuilt(self):
        self._import()
        a, copy, b, c, d = (Email.objects.get(message_id=m) for m in ("<a@x>", "<a-copy@x>", "<b@x>", "<c@x>", "<d@x>"))
        self.assertEqual(c.in_reply_to_id, b.id)  # resolu malgre l'ordre d'import
        self.assertIn(b.in_reply_to_id, {a.id, copy.id})  # deduit par sujet + participants
        self.assertIsNotNone(b.thread_id)
        self.assertEqual(a.thread_id, b.thread_id)
        self.assertEqual(copy.thread_id, b.thread_id)
        self.assertNotEqual(d.thread_id, b.thread_id)
        self.assertEqual(Thread.objects.get(id=b.thread_id).email_count, 2)  # a et sa copie comptent pour 1
        self.assertFalse(Email.objects.filter(subject="Enron Mentions").exclude(thread=None).exists())

    def test_build_threads_is_idempotent(self):
        self._import()
        first = sorted(Email.objects.values_list("id", "in_reply_to_id"))
        call_command("build_threads", stdout=StringIO())
        call_command("build_threads", stdout=StringIO())
        self.assertEqual(sorted(Email.objects.values_list("id", "in_reply_to_id")), first)
        self.assertEqual(Thread.objects.count(), 1)


class WindowsPathTests(TestCase):
    def test_trailing_dot_kept_on_windows(self):
        from unittest import mock

        from discovery.management.commands import import_enron

        with mock.patch.object(import_enron.os, "name", "nt"):
            self.assertEqual(
                import_enron.os_path(r"C:\Users\x\maildir\allen-p\inbox\1."),
                r"\\?\C:\Users\x\maildir\allen-p\inbox\1.",
            )
            self.assertEqual(import_enron.os_path(r"\\srv\share\1."), r"\\?\UNC\srv\share\1.")
        self.assertEqual(import_enron.os_path("/data/maildir/1."), "/data/maildir/1.")
