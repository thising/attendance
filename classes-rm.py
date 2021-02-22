from django.db import models
from manage.models import *

with open("./db-class-rm.data", "r") as f:
    buf = f.read()
    classes = buf.split("\n") 
    for c in classes:
        # parse classes to remove
        inclass = Class.objects.get(classname = c)
        students = Student.objects.filter(inclass = inclass)
        for s in students:
            # remove reports
            records = Report.objects.filter(student = s)
            for i in records:
                i.delete()

            #remove summary count
            records = SummaryCount.objects.filter(student = s)
            for i in records:
                i.delete()

            # remove students
            s.delete()

        # remove activities
        records = Activity.objects.filter(inclass = inclass)
        for i in records:
            i.delete()

        # remove classes
        c.delete()