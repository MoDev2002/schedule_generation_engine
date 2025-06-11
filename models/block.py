# models/block.py

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Union

from models.halls import Hall
from models.labs import Lab
from models.staff_members import Lecturer, TeachingAssistant
from models.time_preferences import TimePreference


class BlockType(Enum):
    LECTURE = "lecture"
    LAB = "lab"


@dataclass
class Block:
    id: str  # unique identifier
    course_code: str
    block_type: BlockType
    staff_member: Union[Lecturer, TeachingAssistant]
    student_count: int
    required_room_type: str  # 'hall' or 'lab'
    group_number: int  # which group this block represents
    total_groups: int  # total number of groups for this course
    is_single_group_course: bool  # if True, no parallel sessions allowed
    academic_list: str  # name of the academic list
    academic_level: int
    practical_in_lab: bool = True
    preferred_rooms: Optional[List[Union[Hall, Lab]]] = None


@dataclass
class Assignment:
    block: Block
    time_slot: TimePreference
    room: Union[Hall, Lab]
