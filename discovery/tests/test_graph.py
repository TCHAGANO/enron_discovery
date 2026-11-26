from django.test import SimpleTestCase

from discovery.graph import pagerank


class PageRankTests(SimpleTestCase):
    def test_empty(self):
        self.assertEqual(pagerank([]), {})

    def test_hub_receives_highest_rank(self):
        edges = [
            {"source": 1, "target": 9, "total": 5},
            {"source": 2, "target": 9, "total": 5},
            {"source": 3, "target": 9, "total": 5},
            {"source": 9, "target": 1, "total": 1},
        ]
        ranks = pagerank(edges)
        self.assertAlmostEqual(sum(ranks.values()), 1.0, places=6)
        self.assertEqual(max(ranks, key=ranks.get), 9)
