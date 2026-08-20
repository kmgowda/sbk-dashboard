# Copyright (c) KMG. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
##

import unittest

from sbk_dashboard.comparison import ComparisonPolicy, ComparisonSelection, comparison_dashboard_uid


class ComparisonPolicyTest(unittest.TestCase):
    def test_policy_owns_complete_descriptor_contract(self):
        policy = ComparisonPolicy(max_targets=6)
        self.assertEqual(
            {
                "minTargets": 1,
                "maxTargets": 6,
                "minSingleTargetTimeLanes": 2,
                "maxTimeLanes": 8,
                "maxTimeGroups": 4,
                "maxAbsoluteRangeDays": 31,
            },
            policy.descriptor(),
        )
        self.assertTrue(policy.matches_descriptor(policy.descriptor()))
        stale = policy.descriptor()
        stale["maxTimeGroups"] = 3
        self.assertFalse(policy.matches_descriptor(stale))

    def test_selection_is_unique_bounded_and_order_independent(self):
        policy = ComparisonPolicy(max_targets=2)
        first = policy.selection(["second", "first"])
        second = policy.selection(["first", "second"])
        self.assertEqual(("first", "second"), first.target_ids)
        self.assertEqual(first.uid, second.uid)
        self.assertEqual(first.uid, comparison_dashboard_uid(["second", "first"]))
        with self.assertRaisesRegex(ValueError, "at least one"):
            policy.selection([])
        with self.assertRaisesRegex(ValueError, "must be unique"):
            policy.selection(["first", "first", "first"])
        with self.assertRaisesRegex(ValueError, "No more than 2"):
            policy.selection(["first", "second", "third"])

    def test_operator_maximum_is_validated_at_policy_construction(self):
        for invalid in (1, 33):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "between 2 and 32"):
                ComparisonPolicy(max_targets=invalid)

    def test_selection_rejects_invalid_direct_construction(self):
        for invalid in (("second", "first"), ("first", "first")):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "sorted, unique"):
                ComparisonSelection(invalid)


if __name__ == "__main__":
    unittest.main()
