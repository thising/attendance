from django.db import models
from manage.models import *

with open("./db-delete.data", "r") as f:
    buf = f.read()
    students = buf.split("\n")
    for s in students:
        student = Student.objects.get(number = s)
        print("***Remove ", student)
        
        records = student.report_set.all()
        for i in records:
            print("- Remove ", i)
            i.delete()

        records = student.summarycount_set.all()
        for i in records:
            print("- Remove ", i)
            i.delete()
        
        student.delete()
        print("***Finish")