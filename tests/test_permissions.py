import unittest

from company_assistant.security.permissions import POLICIES, Actor, can_call


class PermissionTests(unittest.TestCase):
    def test_entire_permission_matrix(self):
        for role in ("employee", "manager", "hr"):
            actor = Actor("E1023", role)
            for name, policy in POLICIES.items():
                for target, manager in (("E1023", None), ("E1024", "E1023"), ("E1050", "E1003")):
                    expected = policy in {"public", "search"} or (
                        policy == "privileged" and role != "employee"
                    )
                    if policy == "employee":
                        expected = (
                            role == "hr"
                            or target == actor.user_id
                            or (role == "manager" and manager == actor.user_id)
                        )
                    with self.subTest(role=role, name=name, target=target):
                        self.assertEqual(can_call(actor, name, target, manager), expected)

    def test_unknown_tools_and_roles_fail_closed(self):
        self.assertFalse(can_call(Actor("E1001", "hr"), "execute_sql"))
        self.assertFalse(can_call(Actor("E1001", "admin"), "get_headcount"))
        self.assertFalse(can_call(None, "get_headcount"))

    def test_unknown_target_is_not_disclosed(self):
        for target in ("E1050", "E9999", None):
            self.assertFalse(can_call(Actor("E1023", "employee"), "get_employee_profile", target))

    def test_no_transitive_manager_access(self):
        self.assertFalse(
            can_call(Actor("E1002", "manager"), "get_employee_projects", "E1050", "E1003")
        )
