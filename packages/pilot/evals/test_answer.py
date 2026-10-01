import unittest

from pilot import answer


class AnswerEval(unittest.TestCase):
    def test_answer_returns_42(self):
        """PILOT-001: answer returns integer 42"""
        result = answer()
        self.assertEqual(result, 42)
        self.assertIs(type(result), int)


if __name__ == "__main__":
    unittest.main()
