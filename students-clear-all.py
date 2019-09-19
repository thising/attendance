from django.db import models
from manage.models import *

records = Report.objects.all()
for i in records:
	i.delete()

records = SummaryCount.objects.all()
for i in records:
	i.delete()

records = Activity.objects.all()
for i in records:
	i.delete()

records = Student.objects.all()
for i in records:
	i.delete()

records = Class.objects.all()
for i in records:
	i.delete()