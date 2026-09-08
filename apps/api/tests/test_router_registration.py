import unittest

from app.main import app


class RouterRegistrationTests(unittest.TestCase):
    def test_no_duplicate_operation_ids(self):
        operation_ids = {}
        for route in app.routes:
            operation_id = getattr(route, "operation_id", None)
            if not operation_id:
                continue
            operation_ids.setdefault(operation_id, []).append(route.path)

        duplicates = {key: paths for key, paths in operation_ids.items() if len(paths) > 1}
        self.assertEqual(duplicates, {}, f"Duplicate FastAPI operation IDs detected: {duplicates}")

    def test_no_duplicate_path_method_registrations(self):
        registrations = {}
        for route in app.routes:
            methods = tuple(sorted(getattr(route, "methods", set())))
            if not methods:
                continue
            key = (route.path, methods)
            registrations.setdefault(key, []).append(getattr(route, "name", "<unnamed>"))

        duplicates = {key: names for key, names in registrations.items() if len(names) > 1}
        self.assertEqual(duplicates, {}, f"Duplicate route registrations detected: {duplicates}")


if __name__ == "__main__":
    unittest.main()
