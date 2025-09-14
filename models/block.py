# models/block.py

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Union

from models.academic_list import AcademicList, Course
from models.halls import Hall
from models.labs import Lab
from models.staff_members import Lecturer, TeachingAssistant
from models.study_plan import CourseAssignment
from models.time_preferences import TimePreference


class BlockType(Enum):
    LECTURE = "lecture"
    LAB = "lab"


@dataclass
class Block:
    id: str  # unique identifier
    course_codes: List[str]  # Always a list, min 1 - same order as academic_lists
    course_objects: List[
        CourseAssignment
    ]  # Always a list, min 1 - same order as academic_lists
    block_type: BlockType
    staff_member: Union[Lecturer, TeachingAssistant]
    student_count: int
    required_room_type: str  # 'hall' or 'lab'
    group_number: int  # which group this block represents
    total_groups: int  # total number of groups for this course
    is_single_group_course: bool  # if True, no parallel sessions allowed
    academic_lists: List[str]  # Academic list names (always a list, min 1)
    academic_list_objects: List[
        AcademicList
    ]  # Academic list objects (always a list, min 1)
    academic_levels: List[int]  # Academic levels in same order as academic_lists
    practical_in_lab: bool = True
    preferred_rooms: Optional[List[Union[Hall, Lab]]] = None
    is_merged_common_course: bool = False  # Flag to identify merged courses


@dataclass
class Assignment:
    block: Block
    time_slot: TimePreference
    room: Union[Hall, Lab]
