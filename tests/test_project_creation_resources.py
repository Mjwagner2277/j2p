"""Creation reuses resource lookups while verifying every task assignment."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from j2p.project import MicrosoftProjectSession, ProjectAutomationError, managed_resource_marker
from test_project_reliability import Assignments
from test_project_verification_scaling import CountedCollection


class RecordingResource:
    def __init__(self, resource_id, name, group='', notes=''):
        object.__setattr__(self, 'writes', [])
        for field, value in (('ID', resource_id), ('Name', name), ('Group', group), ('Notes', notes)):
            object.__setattr__(self, field, value)

    def __setattr__(self, field, value):
        self.writes.append((field, value))
        object.__setattr__(self, field, value)


class CountingResources(CountedCollection):
    def Add(self, name):
        resource = RecordingResource(len(self.items) + 1, name)
        self.items.append(resource)
        return resource


class ProjectCreationResourceTests(unittest.TestCase):
    def fixture(self, resources=()):
        session = object.__new__(MicrosoftProjectSession)
        session.project = SimpleNamespace(Resources=CountingResources(resources))
        return session

    def task(self):
        return SimpleNamespace(Assignments=Assignments(), ResourceNames='')

    def test_many_created_tasks_share_one_resource_scan_and_group_write(self):
        resource = RecordingResource(1, 'Team A', 'Team A', managed_resource_marker('Team A'))
        session = self.fixture([resource])
        tasks = [self.task() for _ in range(250)]
        with patch.object(session, 'verify_managed_resource_assignment',
                          wraps=session.verify_managed_resource_assignment) as verify:
            with session.cache_resources():
                for task in tasks:
                    self.assertTrue(session.set_native_resource_group(task, 'Team A'))
                self.assertEqual(verify.call_count, len(tasks))
                self.assertFalse(hasattr(session, '_update_values'))
        self.assertEqual(session.project.Resources.count_reads, 1)
        self.assertEqual(session.project.Resources.item_reads, 1)
        self.assertEqual(resource.writes, [('Group', 'Team A')])
        self.assertTrue(all([assignment.ResourceID for assignment in task.Assignments.items] == [1]
                            for task in tasks))

    def test_new_resources_refresh_cached_id_lookup_and_preserve_unmanaged_assignments(self):
        person = RecordingResource(1, 'Person', 'People', 'Human notes')
        session = self.fixture([person])
        task = self.task()
        task.Assignments.Add(person.ID)
        with session.cache_resources():
            # Populate the ID map before new managed resources exist.
            self.assertEqual(session.resource_assignments(task)[0][1], person)
            session.set_native_resource_group(task, 'Team A')
            session.set_native_resource_group(task, 'Team B')
            assignments = session.resource_assignments(task)
            self.assertEqual([resource.Group for _, resource in assignments], ['People', 'Team B'])
            self.assertEqual(set(session._update_resources_by_id), {1, 2, 3})
            self.assertIn('Preserved unmanaged', session.resource_assignment_warnings[0])
            session.verify_managed_resource_assignment(task, 'Team B')
        self.assertEqual(session.project.Resources.count_reads, 1)
        self.assertEqual(session.project.Resources.item_reads, 1)
        self.assertEqual(person.writes, [])

    def test_assignment_that_is_not_retained_is_still_rejected(self):
        session = self.fixture()
        task = self.task()
        task.Assignments.Add = lambda ResourceID: SimpleNamespace(ResourceID=ResourceID)
        with session.cache_resources(), self.assertRaisesRegex(ProjectAutomationError, 'managed resource assignment'):
            session.set_native_resource_group(task, 'Team A')

    def test_cached_group_does_not_hide_changed_resource_ownership(self):
        session = self.fixture()
        with session.cache_resources():
            session.set_native_resource_group(self.task(), 'Team A')
            session.project.Resources.items[0].Notes = 'Human notes'
            with self.assertRaisesRegex(ProjectAutomationError, 'managed resource assignment'):
                session.set_native_resource_group(self.task(), 'Team A')

    def test_context_restores_prior_attributes_after_success_and_failure(self):
        names = ('_resource_cache_active', '_update_resources', '_update_resources_by_id',
                 '_update_group_resources')
        for fail in (False, True):
            with self.subTest(fail=fail):
                session = self.fixture()
                previous = dict(zip(names, (False, [], {}, {})))
                for name, value in previous.items():
                    setattr(session, name, value)
                try:
                    with session.cache_resources():
                        session.set_native_resource_group(self.task(), 'Team A')
                        if fail:
                            raise RuntimeError('injected write failure')
                except RuntimeError:
                    self.assertTrue(fail)
                for name, value in previous.items():
                    self.assertIs(getattr(session, name), value)

    def test_context_removes_temporary_attributes_on_failure_without_field_skipping(self):
        session = self.fixture()
        task = RecordingResource(1, 'Same name')
        with self.assertRaisesRegex(RuntimeError, 'injected'):
            with session.cache_resources():
                session.write_task_value(task, 'Name', 'Same name', 'epic=TEAM-1')
                session.write_task_value(task, 'Name', 'Same name', 'epic=TEAM-1')
                raise RuntimeError('injected')
        self.assertEqual(task.writes, [('Name', 'Same name'), ('Name', 'Same name')])
        self.assertFalse(hasattr(session, '_update_values'))
        self.assertFalse(any(hasattr(session, name) for name in (
            '_resource_cache_active', '_update_resources', '_update_resources_by_id', '_update_group_resources')))


if __name__ == '__main__':
    unittest.main()
