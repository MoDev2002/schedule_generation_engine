"""Reconstruct latest lecture run; inspect current-data remedies offline."""
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from managers.constraint_manager import ConstraintManager
from managers.resource_manager import ResourceManager
from models.academic_list import AcademicList
from models.block import Assignment
from models.study_plan import CourseAssignment, StudyPlan
from models.time_preferences import Day, ReservedPeriod
from scheduler import SchedulingEngine
from utils.api_halls import convert_api_hall
from utils.api_staff import convert_api_staff_member
from utils.common_course_processor import ParentReferenceProcessor
from utils.time_utils import convert_api_day_to_enum, convert_api_time_to_time_object

logging.disable(logging.CRITICAL)
ROOT = Path(__file__).resolve().parents[1]


def load(split_cs430=False):
    data = json.loads((ROOT/'audit/latest_run_current_resources.json').read_text(encoding='utf-8'))
    raw = json.loads((ROOT/'audit/latest_run_plans_from_log.json').read_text(encoding='utf-8'))
    if split_cs430:
        for p in raw:
            for c in p['courseAssignments']:
                if p['id']==100 and c['id']==695:
                    assert len(c['lecturers'])==1
                    c['lecture_groups']=2
                    c['lecturers'][0]['num_groups']=2
    needed = {s['id'] for p in raw for c in p['courseAssignments'] for s in c['lecturers']}
    staff = {r['id']: convert_api_staff_member(r) for r in data['lecturers'] if r['id'] in needed}
    plans = []
    for p in raw:
        academic = SimpleNamespace(id=p['academicList']['id'], name=p['academicList']['name'], courses=[])
        courses = [CourseAssignment(
            id=c['id'], course_id=c['course_id'], course_code=c['courseCode'],
            lecture_groups=c['lecture_groups'],
            lecturers=[{'lecturer':staff[s['id']], 'num_of_groups':s['num_groups']} for s in c['lecturers']],
            is_common=c['is_common'], common_study_plan_id=c['common_study_plan_id'],
            common_course_id=c['common_course_id'],
        ) for c in p['courseAssignments']]
        plans.append(StudyPlan(p['id'],p['name'],academic,int(re.search(r'\d+',p['academicLevel']).group()),p['expectedStudents'],courses))
    plans = ParentReferenceProcessor().process_common_courses(plans)
    courses=[]; mapping={}
    for p in plans:
        for c in p.course_assignments:
            mapping[len(courses)]=p;courses.append(c)
    # These six rooms are explicitly logged as the selected pool for this run.
    names={'201B','202B','203B','204B','206B','302'}
    halls=[convert_api_hall(r) for r in data['halls'] if r['name'] in names]
    period=data['schedule-settings']['reserved_period']
    reserved=ReservedPeriod(convert_api_day_to_enum(period['day']),convert_api_time_to_time_object(period['start_time']),convert_api_time_to_time_object(period['end_time']))
    cm=ConstraintManager();rm=ResourceManager(halls,[],reserved);eng=SchedulingEngine(cm,rm)
    blocks=eng.build_blocks(courses,mapping,include_labs=False)
    by_id={b.id:b for b in blocks}
    rows=json.loads((ROOT/'reports/schedule_20260908_200912.json').read_text(encoding='utf-8'))['schedule']
    halls_by_id={h.id:h for h in halls}
    from models.time_preferences import TimePreference
    for row in rows:
        t=row['time_slot'];b=by_id[row['block_id']]
        a=Assignment(b,TimePreference(Day[t['day']],convert_api_time_to_time_object(t['start_time']),convert_api_time_to_time_object(t['end_time'])),halls_by_id[row['room']['id']])
        assert cm.make_assignment(b.id,a),b.id
    assert len(blocks)==(89 if split_cs430 else 88) and len(rows)==86
    return eng,blocks,raw


def describe(a):
    return {'id':a.block.id,'course':a.block.course_codes[0],'group':a.block.group_number,
            'staff_id':a.block.staff_member.id,'room':a.room.name,
            'day':a.time_slot.day.name,'start':str(a.time_slot.start_time)}


def analyze():
    eng,blocks,raw=load();assignments=eng.constraint_manager.get_all_assignments()
    result={'run':'20260908_200912','expected':len(blocks),'placed':len(assignments),'staff_loads':[],'missing':[]}
    for sid in sorted({b.staff_member.id for b in blocks}):
        group=[b for b in blocks if b.staff_member.id==sid];s=group[0].staff_member
        slots={t for b in group for room in eng.resource_manager.get_suitable_rooms(b)
               for t in eng.resource_manager.get_available_slots(b,room,{})}
        result['staff_loads'].append({'id':sid,'name':s.name,'required':len(group),'available_periods':len(slots),
                                     'courses':dict(Counter(b.course_codes[0] for b in group))})
    for b in blocks:
        if b.id in assignments:continue
        rooms=eng.resource_manager.get_suitable_rooms(b)
        entry={'id':b.id,'course':b.course_codes[0],'staff':b.staff_member.name,'staff_id':b.staff_member.id,
               'students':b.student_count,'groups':b.total_groups,'cohorts':list(zip(b.academic_lists,b.academic_levels)),
               'rooms':[r.name for r in rooms],'existing_options':[],'additional_staff_hours_options':[]}
        for room in rooms:
            for t in eng.resource_manager.get_available_slots(b,room,{}):
                causes=eng._conflict_causes(b,t,room,assignments)
                entry['existing_options'].append({'room':room.name,'day':t.day.name,'start':str(t.start_time),
                    'blockers':[describe(assignments[x]) for x in sorted(causes)]})
        prefs=b.staff_member.timing_preferences
        b.staff_member.timing_preferences=[]
        try:
            for room in rooms:
                for t in eng.resource_manager.get_available_slots(b,room,assignments):
                    if eng.constraint_manager.can_assign(b,t,room)[0]:
                        entry['additional_staff_hours_options'].append({'room':room.name,'day':t.day.name,'start':str(t.start_time),'end':str(t.end_time)})
        finally:b.staff_member.timing_preferences=prefs
        result['missing'].append(entry)
    return result


def verify_remedy(split_cs430=False):
    from models.time_preferences import TimePreference
    from schedule_validator import ScheduleValidator
    eng,blocks,_=load(split_cs430)
    additions={93:TimePreference(Day.SUNDAY,convert_api_time_to_time_object('11:00'),convert_api_time_to_time_object('13:00')),
               146:TimePreference(Day.WEDNESDAY,convert_api_time_to_time_object('09:00'),convert_api_time_to_time_object('11:00'))}
    staff={b.staff_member.id:b.staff_member for b in blocks}
    for sid,t in additions.items():staff[sid].timing_preferences.append(t)
    original_ids=set(eng.constraint_manager.get_all_assignments())
    original_placements={k:describe(a) for k,a in eng.constraint_manager.get_all_assignments().items()}
    missing=[b for b in blocks if b.id not in original_ids]
    # Place the two original missing sessions explicitly in checked free slots.
    for b in missing:
        if b.staff_member.id not in additions:continue
        room=next(r for r in eng.resource_manager.halls.values() if r.name=='206B')
        a=Assignment(b,additions[b.staff_member.id],room)
        assert a.time_slot in eng.resource_manager.get_available_slots(b,room,eng.constraint_manager.get_all_assignments())
        assert eng.constraint_manager.make_assignment(b.id,a)
    # If requested, find a slot for the second, 215-student CS430 lecture group.
    for b in missing:
        if b.id in eng.constraint_manager.get_all_assignments():continue
        a,failure=eng._schedule_single_block(b)
        if a is not None:
            assert eng.constraint_manager.make_assignment(b.id,a)
        else:
            assert eng._try_displacement_repair(b,{}), failure
    assignments=eng.constraint_manager.get_all_assignments()
    _,conflicts=ScheduleValidator().validate_schedule_comprehensive(assignments)
    blocking,advisory=ScheduleValidator.split_conflicts(conflicts)
    assert not blocking
    assert len(assignments)==len(blocks)
    if split_cs430:assert not advisory
    return {'split_cs430':split_cs430,'placed':len(assignments),'expected':len(blocks),
            'blocking_conflicts':len(blocking),'advisory_conflicts':len(advisory),
            'moved_existing':[{'before':original_placements[k],'after':describe(a)}
                              for k,a in assignments.items() if k in original_placements
                              and original_placements[k]!=describe(a)],
            'new_placements':[describe(a) for k,a in assignments.items() if k not in original_ids],
            'cs430':[dict(describe(a),students=a.block.student_count) for a in assignments.values() if a.block.course_codes[0]=='CS430']}


if __name__=='__main__':
    result=analyze()
    result['verified_remedies']=[verify_remedy(),verify_remedy(split_cs430=True)]
    (ROOT/'audit/latest_run_diagnosis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))
