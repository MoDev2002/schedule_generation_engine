from dataclasses import dataclass, field
from typing import List, Optional

from models.department import Department


@dataclass
class Course:
    code: str
    name_en: str
    name_ar: str
    lecture_hours: int
    practical_hours: int
    credit_hours: int
    prerequisite_course: Optional[str] = None

    def __post_init__(self):
        if self.lecture_hours < 0 or self.practical_hours < 0 or self.credit_hours < 0:
            raise ValueError("Hours cannot be negative")
        if self.credit_hours != self.lecture_hours + (self.practical_hours / 2):
            raise ValueError(
                "Credit hours cannot be less than sum of lecture and practical hours"
            )


@dataclass
class AcademicList:
    name: str
    department: Department
    courses: List[Course] = field(default_factory=list)

    def __post_init__(self):
        if not self.name.strip():
            raise ValueError("Academic list must have a name")
        if not self.courses:
            raise ValueError("Academic list must have at least one course")


def print_course(course: Course):
    print(
        f"- {course.name_en} ({course.code}): "
        f"{course.lecture_hours} lecture hours / {course.practical_hours} practical hours / {course.credit_hours} credit hours "
    )


def get_course_by_code(academic_list: AcademicList, course_code: str):
    """
    Search for a course in the courses list by its code and return the Course object.

    Args:
        courses: The list of Course objects to search in.
        course_code: The code of the course to search for.

    Returns:
        The matching Course object or None if not found.
    """
    for course in academic_list.courses:
        if course.code == course_code:
            return course
    return None


ai_courses = [
    Course(
        code="UNV101",
        name_en="Societal Issues",
        name_ar="قضايا المجتمع",
        lecture_hours=2,
        practical_hours=0,
        credit_hours=2,
    ),
    Course(
        code="UNV102",
        name_en="English Language",
        name_ar="اللغة الإنجليزية",
        lecture_hours=2,
        practical_hours=0,
        credit_hours=2,
    ),
    Course(
        code="UNV103",
        name_en="Technical and Scientific Writing",
        name_ar="الكتابة الفنية والعلمية",
        lecture_hours=2,
        practical_hours=0,
        credit_hours=2,
    ),
    Course(
        code="UNV104",
        name_en="Artificial Intelligence and Digital Transformation in Society",
        name_ar="الذكاء الاصطناعي والتحول الرقمي في المجتمع",
        lecture_hours=2,
        practical_hours=0,
        credit_hours=2,
    ),
    Course(
        code="UNV105",
        name_en="Effective Communication and Skills",
        name_ar="مهارات الاتصال الفعال",
        lecture_hours=2,
        practical_hours=0,
        credit_hours=2,
    ),
    Course(
        code="BS101",
        name_en="Mathematics in Computer Science",
        name_ar="الرياضيات في علوم الحاسب",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT101",
        name_en="Electronics",
        name_ar="الإلكترونيات",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="BS102",
        name_en="Discrete Structures",
        name_ar="الهياكل المنفصلة",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="BS103",
        name_en="Linear Algebra",
        name_ar="الجبر الخطي",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="BS104",
        name_en="Probability and Statistics Applications in Computers",
        name_ar="الاحتمالات والإحصاء وتطبيقاته في الحاسبات",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS101",
        name_en="Computer Science Fundamentals",
        name_ar="أساسيات علوم الحاسب",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS102",
        name_en="Structured Programming",
        name_ar="البرمجة الهيكلية",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
        prerequisite_course="CS101",
    ),
    Course(
        code="CS103",
        name_en="Object Programming",
        name_ar="البرمجة الشيئية",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
        prerequisite_course="CS102",
    ),
    Course(
        code="IS202",
        name_en="Database Systems",
        name_ar="نظم قواعد البيانات",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT202",
        name_en="Data Communication",
        name_ar="تراسل البيانات",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
        prerequisite_course="IS202",
    ),
    Course(
        code="IT203",
        name_en="Computer Networks",
        name_ar="شبكات الحاسب",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
        prerequisite_course="IT202",
    ),
    Course(
        code="CS204",
        name_en="Logic Design",
        name_ar="التصميم المنطقي",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
        prerequisite_course="CS102",
    ),
    Course(
        code="CS205",
        name_en="Data Structures",
        name_ar="هياكل البيانات",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
        prerequisite_course="CS102",
    ),
    Course(
        code="CS206",
        name_en="Introduction to Artificial Intelligence",
        name_ar="مقدمة في الذكاء الاصطناعي",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
        prerequisite_course="CS205",
    ),
    Course(
        code="CS308",
        name_en="Design and Analysis of Algorithms",
        name_ar="تصميم وتحليل خوارزميات",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
        prerequisite_course="CS205",
    ),
    Course(
        code="CS311",
        name_en="Software Engineering",
        name_ar="هندسة البرمجيات",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
        prerequisite_course="CS308",
    ),
    Course(
        code="CS318",
        name_en="Logic Programming",
        name_ar="البرمجة المنطقية",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
        prerequisite_course="CS311",
    ),
    Course(
        code="CS300",
        name_en="Soft Computing",
        name_ar="الحوسبة المرنة",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
        prerequisite_course="CS311",
    ),
    Course(
        code="IT204",
        name_en="Internet Technology",
        name_ar="تكنولوجيا الانترنت",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IS205",
        name_en="Software Project Management",
        name_ar="إدارة مشاريع البرمجيات",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT305",
        name_en="Signals and Systems",
        name_ar="إشارات ونظم",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT309",
        name_en="Digital Signal Processing",
        name_ar="معالجة الإشارات الرقمية",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS317",
        name_en="Advanced Artificial Intelligence",
        name_ar="الذكاء الاصطناعي المتقدم",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS318",
        name_en="Web Applications",
        name_ar="تطبيقات الويب",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IS323",
        name_en="Human Computer Interaction",
        name_ar="تفاعل الإنسان مع الحاسب",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="BS211",
        name_en="Professional Ethics for Computer Science",
        name_ar="الأخلاق المهنية لعلوم الحاسب",
        lecture_hours=2,
        practical_hours=0,
        credit_hours=2,
    ),
    Course(
        code="CS321",
        name_en="Evolutionary and Swarm Intelligence",
        name_ar="الذكاء التطوري والسرب",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT419",
        name_en="Speech Processing",
        name_ar="معالجة الكلام",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT423",
        name_en="Internet of Things",
        name_ar="أنترنت الأشياء",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IS426",
        name_en="Big Data Analytics",
        name_ar="تحليل البيانات الكبيرة",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IS430",
        name_en="Data Visualization",
        name_ar="العرض المرئي للبيانات",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS429",
        name_en="Cryptography",
        name_ar="التشفير",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS432",
        name_en="Computer Vision",
        name_ar="الرؤية بالحاسب",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS435",
        name_en="Smart Applications",
        name_ar="التطبيقات الذكية",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS436",
        name_en="Natural Language Processing",
        name_ar="معالجة اللغات الطبيعية",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS437",
        name_en="Data Science",
        name_ar="علم البيانات",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS443",
        name_en="Artificial Intelligence for Robotics",
        name_ar="الذكاء الاصطناعي للروبوت",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS444",
        name_en="Robot Programming",
        name_ar="برمجة الروبوت",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IS309",
        name_en="Information Security",
        name_ar="تأمين المعلومات",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS322",
        name_en="Probabilistic Graphical Models",
        name_ar="النماذج الرسومية الاحتمالية",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS323",
        name_en="Decision Making under Uncertainty",
        name_ar="صنع القرار في ظل عدم اليقين",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS324",
        name_en="Advanced Machine Learning",
        name_ar="تعلم الآلة المتقدم",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS325",
        name_en="Deep Generative Models",
        name_ar="النماذج العميقة التوليدية",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS326",
        name_en="Reinforcement Learning",
        name_ar="التعلم المعزز",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS327",
        name_en="Programming for Problem Solving",
        name_ar="البرمجة لحل المشكلات",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS328",
        name_en="Agent-Based Modelling",
        name_ar="النمذجة القائمة على الوكيل",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT416",
        name_en="Virtual and Augmented Reality",
        name_ar="الواقع الافتراضي والمعزز",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT418",
        name_en="Embedded Systems",
        name_ar="النظم المدمجة",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT426",
        name_en="Robotics Vision",
        name_ar="رؤية الروبوت",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT427",
        name_en="Introduction to Autonomous Vehicles",
        name_ar="مقدمة في المركبات ذاتية القيادة",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT428",
        name_en="Mobile and Cyber-Physical Systems",
        name_ar="الأنظمة المتنقلة والسيبرانية الفيزيائية",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT429",
        name_en="Decentralized Artificial Intelligence techniques",
        name_ar="تقنيات الذكاء الاصطناعي اللامركزية",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IS431",
        name_en="Knowledge Representation",
        name_ar="تمثيل المعرفة",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT430",
        name_en="Applied Robotics",
        name_ar="الروبوتات التطبيقية",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IS432",
        name_en="Information Systems Innovation and New Technologies",
        name_ar="الابتكار في نظم المعلومات والتقنيات الجديدة",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT431",
        name_en="Robotic Simulation",
        name_ar="المحاكاة الروبوتية",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT432",
        name_en="Robot Modelling and Control",
        name_ar="نمذجة الروبوتات والتحكم فيها",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="IT433",
        name_en="Robot Motion Planning",
        name_ar="تخطيط حركة الروبوت",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS434",
        name_en="Pattern Recognition",
        name_ar="التعرف على الأنماط",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS438",
        name_en="Computer Arabization",
        name_ar="تعريب الحاسبات",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS439",
        name_en="Computer Animations",
        name_ar="الرسوم المتحركة بالحاسب",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS440",
        name_en="Deep Learning",
        name_ar="التعلم العميق",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS441",
        name_en="Computational Intelligence",
        name_ar="الذكاء الحسابي",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS442",
        name_en="Intelligent Agents based Systems",
        name_ar="الوكلاء الأذكياء",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS445",
        name_en="Computational Cognitive Science",
        name_ar="علم الحوسبة الإدراكي",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS446",
        name_en="Autonomous Agents",
        name_ar="وكلاء التحكم الذاتي",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
    Course(
        code="CS450",
        name_en="New Trends in Artificial Intelligence",
        name_ar="اتجاهات جديدة في الذكاء الاصطناعي",
        lecture_hours=2,
        practical_hours=2,
        credit_hours=3,
    ),
]

ai_academic_list = AcademicList(
    name="Artificial Intelligence Academic List",
    department=Department.ARTIFICIAL_INTELLIGENCE,
    courses=ai_courses,
)
