from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from discovery.models import Attachment, Email, Employee, Folder
from discovery.search import update_search_vectors


class EnronViewsTestCase(TestCase):
    @staticmethod
    def _ctx(response, key, default=None):
        # Standard Django test response context
        if response.context is not None:
            try:
                return response.context[key]
            except (KeyError, TypeError):
                pass

            # ContextList fallback
            try:
                for layer in response.context:
                    if hasattr(layer, "get") and key in layer:
                        return layer[key]
            except TypeError:
                pass

        # TemplateResponse fallback
        context_data = getattr(response, "context_data", None)
        if isinstance(context_data, dict) and key in context_data:
            return context_data[key]

        return default

    def _emails_from_response(self, response):
        emails = self._ctx(response, "emails")
        if emails is not None:
            return emails

        page_obj = self._ctx(response, "page_obj")
        if page_obj is not None:
            return page_obj.object_list

        object_list = self._ctx(response, "object_list")
        if object_list is not None:
            return object_list

        self.fail("No email collection found in response context")

    @classmethod
    def setUpTestData(cls):
        cls.folder = Folder.objects.create(name="inbox", mailbox="alice-a", path="alice-a/inbox")
        cls.other_folder = Folder.objects.create(name="inbox", mailbox="bob-b", path="bob-b/inbox")

        cls.alice = Employee.objects.create(name="Alice", email="alice@enron.com")
        cls.bob = Employee.objects.create(name="Bob", email="bob@enron.com")
        cls.charlie = Employee.objects.create(name="Charlie", email="charlie@enron.com")

        now = timezone.now()

        # Root email for thread
        cls.root_email = Email.objects.create(
            message_id="<msg-root@enron>",
            date=now - timedelta(days=3),
            subject="Energy strategy Q1",
            body="Discussion about market strategy and energy contracts.",
            from_employee=cls.alice,
            folder=cls.folder,
        )
        cls.root_email.to_employees.add(cls.bob)

        # Reply 1
        cls.reply_1 = Email.objects.create(
            message_id="<msg-reply-1@enron>",
            date=now - timedelta(days=2),
            subject="Re: Energy strategy Q1",
            body="I agree with the proposed market strategy.",
            from_employee=cls.bob,
            folder=cls.folder,
            in_reply_to=cls.root_email,
        )
        cls.reply_1.to_employees.add(cls.alice)

        # Reply 2 (child of reply_1)
        cls.reply_2 = Email.objects.create(
            message_id="<msg-reply-2@enron>",
            date=now - timedelta(days=1),
            subject="Re: Energy strategy Q1 - follow-up",
            body="Adding legal review and compliance checks.",
            from_employee=cls.charlie,
            folder=cls.folder,
            in_reply_to=cls.reply_1,
        )
        cls.reply_2.to_employees.add(cls.alice, cls.bob)

        # Unrelated email
        cls.other_email = Email.objects.create(
            message_id="<msg-other@enron>",
            date=now,
            subject="Cafeteria menu",
            body="Lunch options for next week. <script>alert(1)</script>",
            from_employee=cls.charlie,
            folder=cls.other_folder,
        )
        cls.other_email.to_employees.add(cls.alice)

        # Populate search_vector for FTS tests
        Attachment.objects.create(email=cls.other_email, filename="menu.pdf", content_type="application/pdf", size=10)
        update_search_vectors(Email.objects.all())

    def setUp(self):
        cache.clear()

    def test_home_page(self):
        response = self.client.get(reverse("enron:home"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("total_emails", response.context)
        self.assertIn("total_employees", response.context)
        self.assertEqual(response.context["total_emails"], 4)

    def test_dashboard_page(self):
        response = self.client.get(reverse("enron:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("chart_labels", response.context)
        self.assertIn("chart_values", response.context)
        self.assertIn("top_senders", response.context)

    def test_search_by_keyword_fts(self):
        response = self.client.get(reverse("enron:search_emails"), {"q": "strategy"})
        self.assertEqual(response.status_code, 200)

        emails = self._emails_from_response(response)
        ids = {e.id for e in emails}

        self.assertIn(self.root_email.id, ids)
        self.assertIn(self.reply_1.id, ids)
        self.assertIn(self.reply_2.id, ids)
        self.assertNotIn(self.other_email.id, ids)

    def test_search_by_sender(self):
        response = self.client.get(
            reverse("enron:search_emails"),
            {"sender": "charlie@enron.com"},
        )
        self.assertEqual(response.status_code, 200)

        emails = self._emails_from_response(response)
        ids = {e.id for e in emails}

        self.assertIn(self.reply_2.id, ids)
        self.assertIn(self.other_email.id, ids)
        self.assertNotIn(self.root_email.id, ids)

    def test_search_without_filters_returns_paginated_results(self):
        response = self.client.get(reverse("enron:search_emails"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(self._ctx(response, "total_results")), "4")
        self.assertTrue(bool(self._ctx(response, "search_hint")))
        self.assertTrue(bool(self._ctx(response, "enable_pagination")))
        self.assertEqual(self._ctx(response, "current_page"), 1)

        emails = self._emails_from_response(response)
        ids = {e.id for e in emails}
        self.assertIn(self.root_email.id, ids)
        self.assertIn(self.reply_1.id, ids)
        self.assertIn(self.reply_2.id, ids)
        self.assertIn(self.other_email.id, ids)

    def test_search_by_date_range(self):
        date_from = (timezone.now() - timedelta(days=2)).date().isoformat()
        response = self.client.get(
            reverse("enron:search_emails"),
            {"date_from": date_from},
        )
        self.assertEqual(response.status_code, 200)

        emails = self._emails_from_response(response)
        ids = {e.id for e in emails}

        self.assertIn(self.reply_1.id, ids)
        self.assertIn(self.reply_2.id, ids)
        self.assertIn(self.other_email.id, ids)
        self.assertNotIn(self.root_email.id, ids)

    def test_thread_detail_builds_full_conversation(self):
        response = self.client.get(
            reverse("enron:thread_detail", args=[self.reply_2.id])
        )
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.context["root"].id, self.root_email.id)
        conversation_ids = [e.id for e in response.context["conversation"]]
        self.assertEqual(
            conversation_ids,
            [self.root_email.id, self.reply_1.id, self.reply_2.id],
        )

    def test_influence_graph(self):
        response = self.client.get(reverse("enron:influence_graph"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("connections", response.context)

        connections = list(response.context["connections"])
        # Verify at least one expected edge exists
        has_alice_to_bob = any(
            c["from_employee__email"] == "alice@enron.com"
            and c["to_employees__email"] == "bob@enron.com"
            for c in connections
        )
        self.assertTrue(has_alice_to_bob)

    def test_email_list_page(self):
        response = self.client.get(reverse("enron:email_list"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("emails", response.context)
        self.assertIn("stats", response.context)
        self.assertEqual(response.context["stats"]["total_emails"], 4)
    # --- Nouvelles fonctionnalites de recherche ---

    def _search_ids(self, **params):
        response = self.client.get(reverse("enron:search_emails"), params)
        self.assertEqual(response.status_code, 200)
        return {e.id for e in self._emails_from_response(response)}, response

    def test_search_highlights_and_escapes(self):
        ids, response = self._search_ids(q="lunch")
        self.assertEqual(ids, {self.other_email.id})
        html = response.content.decode()
        self.assertIn("<mark>Lunch</mark>", html)
        self.assertNotIn("<script>alert(1)</script>", html)

    def test_search_websearch_syntax(self):
        ids, _ = self._search_ids(q="strategy -legal")
        self.assertEqual(ids, {self.root_email.id, self.reply_1.id})
        ids, _ = self._search_ids(q="cafeteria OR compliance")
        self.assertEqual(ids, {self.other_email.id, self.reply_2.id})

    def test_search_stemming_english(self):
        ids, _ = self._search_ids(q="contract")
        self.assertIn(self.root_email.id, ids)

    def test_search_by_recipient_has_no_duplicates(self):
        response = self.client.get(reverse("enron:search_emails"), {"recipient": "alice"})
        emails = list(self._emails_from_response(response))
        self.assertEqual(len(emails), len({e.id for e in emails}))
        self.assertEqual({e.id for e in emails}, {self.reply_1.id, self.reply_2.id, self.other_email.id})

    def test_search_by_mailbox_and_attachments(self):
        ids, _ = self._search_ids(mailbox="bob-b")
        self.assertEqual(ids, {self.other_email.id})
        ids, _ = self._search_ids(attachments="1")
        self.assertEqual(ids, {self.other_email.id})

    def test_search_invalid_date_does_not_crash(self):
        _ids, response = self._search_ids(date_from="2001-13-45")
        self.assertIn("Date de debut invalide.", response.context["errors"])

    def test_search_pagination_keeps_filters(self):
        for i in range(25):
            Email.objects.create(
                message_id=f"<bulk-{i}@enron>", subject="Bulk & co", from_employee=self.alice, folder=self.folder
            )
        response = self.client.get(reverse("enron:search_emails"), {"sender": "alice@enron.com"})
        self.assertTrue(response.context["has_next"])
        self.assertIn("sender=alice%40enron.com&amp;page=2", response.content.decode())

    def test_influence_graph_ranking_and_nodes(self):
        response = self.client.get(reverse("enron:influence_graph"), {"limit": "50"})
        self.assertEqual(response.status_code, 200)
        ranking = response.context["ranking"]
        self.assertEqual(ranking[0]["email"], "alice@enron.com")  # tout le monde ecrit a Alice
        self.assertEqual(len(response.context["nodes"]), 3)

    def test_influence_graph_user_filter(self):
        response = self.client.get(reverse("enron:influence_graph"), {"user": "bob"})
        for conn in response.context["connections"]:
            self.assertTrue("bob" in conn["from_employee__email"] or "bob" in conn["to_employees__email"])

    def test_thread_detail_uses_reconstructed_thread(self):
        from discovery.threads import build_threads

        build_threads()
        response = self.client.get(reverse("enron:thread_detail", args=[self.reply_1.id]))
        conversation_ids = [e.id for e in response.context["conversation"]]
        self.assertEqual(conversation_ids[:2], [self.root_email.id, self.reply_1.id])
        self.assertNotIn(self.other_email.id, conversation_ids)

    def test_dashboard_is_cached(self):
        self.client.get(reverse("enron:dashboard"))
        with self.assertNumQueries(0):
            self.client.get(reverse("enron:dashboard"))
