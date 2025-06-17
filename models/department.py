# models/department.py

from dataclasses import dataclass


@dataclass
class Department:
    id: int
    name: str

    def __str__(self):
        return self.name
