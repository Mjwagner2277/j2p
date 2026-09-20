"""Project object-model fake populated from the real yerp plan; never a live MPP."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from j2p.core import build_run_plan
from j2p.jira import read_csv_rows
from j2p.project import MicrosoftProjectSession, managed_resource_marker, project_date_for_com, summary_assignments
from j2p.project_values import epic_assignments
from test_project_update_integration import RecordingTask
from test_project_verification_scaling import CountedCollection

ROOT = Path(__file__).resolve().parents[1]
YERP = ROOT / 'yerp'
FILES = sorted(YERP.glob('*.csv'))


def changed_child_plan(config, baseline=None):
    """Exercise a real task estimate/status change without writing source CSVs."""
    changed = []

    def read_changed(path, *args, **kwargs):
        rows, encoding = read_csv_rows(path, *args, **kwargs)
        headers = rows[0]
        key_index = headers.index('Issue key')
        for row in rows[1:]:
            if row[key_index] == 'SSWSW-11845':
                row[headers.index('Custom field (Story Points)')] = '5'
                row[headers.index('Status')] = 'Done'
                changed.append(row[key_index])
        return rows, encoding

    with patch('j2p.jira.read_csv_rows', side_effect=read_changed):
        plan = build_run_plan(FILES, config, baseline)
    if changed != ['SSWSW-11845']:
        raise AssertionError('Expected the actual child SSWSW-11845 exactly once')
    return plan


class SummaryDateTask(SimpleNamespace):
    """Model native summary dates and the danger of editing their text values."""

    def __init__(self, **values):
        start, finish = values.pop('Start'), values.pop('Finish')
        super().__init__(**values)
        self._start_native, self._finish_native = start, finish

    @property
    def Start(self):
        return self._start_native

    @property
    def Finish(self):
        return self._finish_native

    @property
    def StartText(self):
        return self.Start.strftime('%B %d, %Y %I:%M %p')

    @StartText.setter
    def StartText(self, value):
        start = datetime.strptime(value, '%B %d, %Y %I:%M %p')
        delta = start - self._start_native
        self._start_native = start
        for child in getattr(self, 'summary_shift_children', ()):
            child.Start += delta
            child.Finish += delta

    @property
    def FinishText(self):
        return self.Finish.strftime('%B %d, %Y %I:%M %p')

    @FinishText.setter
    def FinishText(self, value):
        self._finish_native = datetime.strptime(value, '%B %d, %Y %I:%M %p')


def project_date_format(value, format_code):
    if format_code != 2:
        raise AssertionError('Expected Project pjDate_mmm_dd_yyyy_hh_mmAM format')
    return value.strftime('%B %d, %Y %I:%M %p')


def project_from_yerp_plan(plan, config, scheduled_dates=False):
    """Represent every real planned row, owned resource, outline, and FS link.

    scheduled_dates models a completed native calculation with valid date
    windows; Jira target fields and the source plan remain unchanged.
    """
    groups = sorted({epic.resource_group for epic in plan.epics.values() if epic.resource_group})
    resources = {
        group: SimpleNamespace(ID=index, Name=group, Group=group, Notes=managed_resource_marker(group))
        for index, group in enumerate(groups, start=1)
    }
    tasks, summaries, epics = [], {}, {}

    def row(**values):
        values.setdefault('ID', len(tasks) + 1)
        values.setdefault('UniqueID', 10000 + len(tasks))
        values.setdefault('Manual', False)
        values.setdefault('Active', True)
        values.setdefault('Flag2', False)
        values.setdefault('HideBar', False)
        values.setdefault('Assignments', CountedCollection())
        values.setdefault('ResourceGroup', '')
        values.setdefault('PercentComplete', 0)
        values.setdefault('Start', project_date_for_com('2026-09-17', 'Start'))
        values.setdefault('Finish', project_date_for_com('2026-09-17', 'Finish'))
        native = SummaryDateTask(**values) if values.get('Summary') else SimpleNamespace(**values)
        task = RecordingTask(native)
        tasks.append(task)
        return task

    for summary in sorted(plan.summaries.values(), key=lambda item: item.summary_id):
        task = row(Summary=True, Manual=False,
                   **dict(summary_assignments(summary, config)))
        summaries[summary.summary_id] = task
    for epic in sorted(plan.epics.values(), key=lambda item: item.key):
        resource = resources.get(epic.resource_group)
        values = dict(epic_assignments(epic, config))
        values.update(Summary=False, Active=epic.drives_schedule,
                      OutlineParent=summaries[f'{epic.rollup_mode}:{epic.rollup_key}'],
                      ResourceGroup=epic.resource_group,
                      Assignments=CountedCollection([SimpleNamespace(ResourceID=resource.ID)] if resource else []),
                      HideBar=bool(epic.completed and config['behavior']['hide_completed_epics']))
        if epic.target_start:
            values[config['project_fields']['jira_target_start']] = project_date_for_com(epic.target_start, 'Start')
            values['Start'] = values[config['project_fields']['jira_target_start']]
        if epic.target_end:
            values[config['project_fields']['jira_target_end']] = project_date_for_com(epic.target_end, 'Finish')
            values['Finish'] = values[config['project_fields']['jira_target_end']]
        epics[epic.key] = row(**values)
    for epic in plan.epics.values():
        task = epics[epic.key]
        predecessors = [epics[key] for key in epic.predecessors]
        task.task.Predecessors = ','.join(str(predecessor.ID) for predecessor in predecessors)
        task.task.PredecessorTasks = CountedCollection(predecessors)
        task.task.SuccessorTasks = CountedCollection([epics[key] for key in epic.successors])
        dependencies = []
        for predecessor in predecessors:
            dependency = SimpleNamespace(To=task, Type=1, Lag=0)
            setattr(dependency, 'From', predecessor)
            dependencies.append(dependency)
        task.task.TaskDependencies = CountedCollection(dependencies)
    if scheduled_dates:
        for epic in plan.epics.values():
            task = epics[epic.key].task
            if epic.drives_schedule and task.Start > task.Finish:
                if not epic.target_start:
                    task.Start = task.Finish.replace(hour=8, minute=0)
                else:
                    task.Finish = task.Start.replace(hour=17, minute=0)
    for epic in plan.epics.values():
        if not epic.drives_schedule:
            primary = epics[epic.primary_schedule_key]
            epics[epic.key].task.Start, epics[epic.key].task.Finish = primary.Start, primary.Finish
    fields = config['project_fields']
    for epic in plan.epics.values():
        primary = epics[epic.key if epic.drives_schedule else epic.primary_schedule_key]
        setattr(epics[epic.key].task, fields['schedule_start'], primary.Start)
        setattr(epics[epic.key].task, fields['schedule_finish'], primary.Finish)
    for summary in plan.summaries.values():
        members = [epics[epic.key if epic.drives_schedule else epic.primary_schedule_key]
                   for epic in plan.epics.values()
                   if epic.rollup_mode == summary.rollup_mode and epic.rollup_key == summary.key]
        task = summaries[summary.summary_id].task
        if members:
            setattr(task, fields['schedule_start'], min(member.Start for member in members))
            setattr(task, fields['schedule_finish'], max(member.Finish for member in members))
        driving = [epics[epic.key] for epic in plan.epics.values()
                   if epic.drives_schedule and epic.rollup_mode == summary.rollup_mode and epic.rollup_key == summary.key]
        if driving:
            task._start_native = min(member.Start for member in driving)
            task._finish_native = max(member.Finish for member in driving)
    session = object.__new__(MicrosoftProjectSession)
    session.project = SimpleNamespace(Tasks=CountedCollection(tasks), Resources=CountedCollection(resources.values()))
    session.app = SimpleNamespace(ActiveProject=session.project)
    session.assert_project_identity = Mock()
    session.recalculate = Mock()  # Native scheduling must still be checked in Windows.
    names = {config['project_fields'][key]: value for key, value in config['project_field_names'].items()}
    session.app.FieldNameToFieldConstant = Mock(side_effect=lambda name: name)
    session.app.CustomFieldGetName = Mock(side_effect=lambda name: names.get(name, ''))
    session.app.CustomFieldRename = Mock()
    session.app.DateFormat = Mock(side_effect=project_date_format)
    return session, epics, summaries
