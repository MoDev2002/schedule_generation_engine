from enum import Enum


class Department(Enum):
    COMPUTER_SCIENCE = "Computer Science"
    INFORMATION_TECHNOLOGY = "Information Technology"
    INFORMATION_SYSTEMS = "Information Science"
    GENERAL = "General"
    ARTIFICIAL_INTELLIGENCE = "Artificial Intelligence"
    CYBERSECURITY = "Cybersecurity"

    def __post_init__(self):
        if self.start_time >= self.end_time:
            raise ValueError("End time must be after start time")


# Example usage:
if __name__ == "__main__":
    pass
