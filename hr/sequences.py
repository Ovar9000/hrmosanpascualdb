"""Race-safe sequence allocation for San Pascual HR records.

Features:
- Atomic generation of formatted employee numbers (e.g. SP-00001).
- Pessimistic locking via select_for_update() inside transaction (DECISIONS S1).
- Self-synchronizing with existing database records on initialization or --clear.
- Excluded from audit signals.
"""

from django.db import transaction
from .models import EmployeeSequence, Person


def sync_employee_sequence():
    """Resynchronizes the EmployeeSequence counter to the highest employee_no in the database."""
    max_num = 0
    for emp in Person.objects.values_list("employee_no", flat=True):
        if emp and emp.startswith("SP-"):
            try:
                num = int(emp.split("-")[1])
                if num > max_num:
                    max_num = num
            except (ValueError, IndexError):
                pass

    seq, _ = EmployeeSequence.objects.get_or_create(
        name="employee_no",
        defaults={"last_number": max_num},
    )
    if seq.last_number != max_num:
        seq.last_number = max_num
        seq.save()
    return seq.last_number


def get_next_employee_no():
    """Generates race-safe sequential employee number like SP-00001 (DECISIONS S1)."""
    with transaction.atomic():
        seq, _ = EmployeeSequence.objects.select_for_update().get_or_create(
            name="employee_no",
            defaults={"last_number": 0},
        )
        if seq.last_number == 0:
            seq.last_number = sync_employee_sequence()

        seq.last_number += 1
        seq.save()
        return f"SP-{seq.last_number:05d}"
