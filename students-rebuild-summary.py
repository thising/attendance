from django.db import models
from manage.models import *

records = Student.objects.all()
for i in records:
    scs = i.summarycount_set.all()
    if scs:
        for sc in scs:
            reports = student.report_set.filter(activity__time__year = sc.year, activity__time__month = sc.month)
            sc.absent_count = reports.filter(status = "absent").count()
            sc.late_count = reports.filter(status = "late").count()
            sc.leave_count = reports.filter(status = "leave").count()
            sc.low_count = reports.filter(level = "low").count()
            sc.mid_count = reports.filter(level = "mid").count()
            sc.high_count = reports.filter(level = "high").count()
            sc.discipline_low_count = reports.filter(discipline = "low").count()
            sc.discipline_mid_count = reports.filter(discipline = "mid").count()
            sc.discipline_high_count = reports.filter(discipline = "high").count()
            sc.save()

    print(i.number, i.name, len(scs))