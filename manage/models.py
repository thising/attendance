# -*- coding:utf-8 -*-

from django.db import models
from django.db.models import Q
from django.contrib.auth.models import User
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

import time
import hashlib
from datetime import datetime

def class_hash():
    """This function generate 10 character long hash"""
    hash = hashlib.sha1()
    hash.update(str(time.time()).encode("utf-8"))
    return  hash.hexdigest()[-10:]

# Create your models here.
# class User(models.Model):
#     username    = models.CharField('用户名', max_length = 20)
#     password    = models.CharField('密码', max_length = 20)
 
#     def __str__(self):
#         return self.username

class Class(models.Model):
    classname   = models.CharField('班级名称', max_length = 30)
    sharecode   = models.CharField('访问码', max_length = 10, default = class_hash, unique = True)
    managecode  = models.CharField('管理密码', max_length = 20)
    owner       = models.ForeignKey(User, models.CASCADE)

    def __str__(self):
        return self.classname + "(" + self.owner.username + ")"

class Student(models.Model):
    enum_sex = (
        ('male', '男'),
        ('female', '女'),
    )

    number  = models.CharField('学号', max_length = 20)
    name    = models.CharField('姓名', max_length = 20)
    sex     = models.CharField('性别', choices = enum_sex, max_length = 8, default = 'male')
    inclass = models.ForeignKey(Class, models.CASCADE)

    def __str__(self):
        return self.number + " | " + self.name + " | " + self.inclass.classname

    def summary_current_month(self):
        today = datetime.now()
        return self.summary_month(year = today.year, month = today.month)

    def summary_last_month(self):
        today = datetime.now()
        y = today.year
        m = today.month - 1
        if m == 0:
            y -= 1
            m = 12
        return self.summary_month(year = y, month = m)

    def summary_term(self):
        today = datetime.now()
        year = today.year
        month = today.month
        if month >= 9:
            month = 9
        elif month >= 2:
            month = 2
        else:
            year -= 1

        # return self.summary_total_avg(2021, 2)
        return self.summary_total_avg(year, month)

    def summary_term_last(self):
        today = datetime.now()
        starty = endy = year = today.year
        startm = endm = month = today.month
        if month >= 2 and month < 9:
            starty -= 1
            startm = 9
            endy = year
            endm = 1
        elif month >= 9:
            starty = endy = year
            startm = 2
            endm = 8
        else:
            starty = endy = year - 1
            startm = 2
            endm = 8
        return self.summary_total_avg_stage(starty, startm, endy, endm)

    def report_term(self):
        today = datetime.now()
        year = today.year
        month = today.month
        if month >= 9:
            month = 9
        elif month >= 2:
            month = 2
        else:
            year -= 1

        return self.report_detail(year, month)

    def report_term_last(self):
        today = datetime.now()
        starty = endy = year = today.year
        startm = endm = month = today.month
        if month >= 2 and month < 9:
            starty -= 1
            startm = 9
            endy = year
            endm = 1
        elif month >= 9:
            starty = endy = year
            startm = 2
            endm = 8
        else:
            starty = endy = year - 1
            startm = 2
            endm = 8

        return self.report_detail_stage(starty, startm, endy, endm)

    def summary_month(self, year, month):
        try:
            sc = self.summarycount_set.get(year = year, month = month)
            return (
                    self.number, 
                    self.name, 
                    self.get_sex_display(), 
                    sc.absent_count, 
                    sc.late_count,
                    sc.leave_count, 
                    sc.low_count, 
                    sc.mid_count, 
                    sc.high_count, 
                    sc.discipline_low_count,
                    sc.discipline_mid_count,
                    sc.discipline_high_count,
                    sc.score()
                )
        except Exception as e:
            return None

    def summary_total_avg(self, year, month):
        scs = self.summarycount_set.filter((Q(year = year) & Q(month__gte = month)) | Q(year__gt = year))
        absent = 0
        late = 0
        leave = 0
        low = 0
        mid = 0
        high = 0
        score = 0
        dlow = 0
        dmid = 0
        dhigh = 0
        for sc in scs:
            absent += sc.absent_count
            late += sc.late_count
            leave += sc.leave_count
            low += sc.low_count
            mid += sc.mid_count
            high += sc.high_count
            dlow += sc.discipline_low_count
            dmid += sc.discipline_mid_count
            dhigh += sc.discipline_high_count
            score += sc.score()
        score = 0 if scs.count() == 0 else (score / scs.count() * 1.0)

        return (
                self.number, 
                self.name, 
                self.get_sex_display(),
                absent, 
                late, 
                leave, 
                low, 
                mid, 
                high, 
                dlow,
                dmid,
                dhigh,
                score,
                absent + late + leave + low + mid + high + dlow + dmid + dhigh > 0,
                self.id
            )

    def summary_total_avg_stage(self, start_year, start_month, end_year, end_month):
        scs = None
        if end_month > start_month:
            scs = self.summarycount_set.filter(Q(year = start_year) & Q(month__gte = start_month) & Q(month__lte = end_month))
        else:
            scs = self.summarycount_set.filter((Q(year = start_year) & Q(month__gte = start_month)) | (Q(year = end_year) & Q(month__lte = end_month)))
        absent = 0
        late = 0
        leave = 0
        low = 0
        mid = 0
        high = 0
        score = 0
        dlow = 0
        dmid = 0
        dhigh = 0
        for sc in scs:
            absent += sc.absent_count
            late += sc.late_count
            leave += sc.leave_count
            low += sc.low_count
            mid += sc.mid_count
            high += sc.high_count
            dlow += sc.discipline_low_count
            dmid += sc.discipline_mid_count
            dhigh += sc.discipline_high_count
            score += sc.score()
        score = 0 if scs.count() == 0 else (score / scs.count() * 1.0)

        return (
                self.number, 
                self.name, 
                self.get_sex_display(),
                absent, 
                late, 
                leave, 
                low, 
                mid, 
                high, 
                dlow,
                dmid,
                dhigh,
                score,
                absent + late + leave + low + mid + high + dlow + dmid + dhigh > 0,
                self.id
            )

    def report_detail(self, year, month):
        ret = []
        reports = self.report_set.filter((Q(activity__time__year = year) & Q(activity__time__month__gte = month)) | Q(activity__time__year__gt = year))
        for item in reports:
            desc = ''
            if item.activity.activity_type == 'activity':
                desc = item.get_level_display()
            elif item.activity.activity_type == 'discipline':
                desc = item.get_discipline_display()
            else:
                desc = item.get_status_display()

            ret += [(item.activity.time.strftime("%Y-%m-%d %H:%M"), item.activity.name, desc, item.activity.activity_type, item.activity.id)]

        return ret

    def report_detail_stage(self, start_year, start_month, end_year, end_month):
        ret = []
        reports = None
        if end_month > start_month:
            reports = self.report_set.filter((Q(activity__time__year = start_year) & Q(activity__time__month__gte = start_month)) & Q(activity__time__month__lte = end_month))
        else:
            reports = self.report_set.filter((Q(activity__time__year = start_year) & Q(activity__time__month__gte = start_month)) | (Q(activity__time__year = end_year) & Q(activity__time__month__lte = end_month)))
        for item in reports:
            desc = ''
            if item.activity.activity_type == 'activity':
                desc = item.get_level_display()
            elif item.activity.activity_type == 'discipline':
                desc = item.get_discipline_display()
            else:
                desc = item.get_status_display()

            ret += [(item.activity.time.strftime("%Y-%m-%d %H:%M"), item.activity.name, desc, item.activity.activity_type)]

        return ret

    class Meta:
        ordering = ["number"]

class Activity(models.Model):
    enum_activity_type = (
        ('class', '考勤'),
        ('activity', '活动'),
        ('discipline', '违纪'),
    )

    enum_status_type = (
        ('preview', '预览'),
        ('release', '发布'),
    )

    time = models.DateTimeField('创建时间', auto_now_add=True)
    activity_type = models.CharField('活动类型', choices = enum_activity_type, max_length = 20, default = 'class')
    name = models.CharField('活动名称', max_length = 30)
    status = models.CharField('活动状态', choices = enum_status_type, max_length = 20, default = 'preview')
    inclass = models.ForeignKey(Class, models.CASCADE)

    def __str__(self):
        return self.time.strftime("%Y-%m-%d %H:%M") + " | " + self.get_activity_type_display() + " | " + self.name

    class Meta:
        ordering = ["-time"]

class Report(models.Model):
    enum_student_status = (
        ('absent', '缺席'),
        ('late', '迟到'),
        ('leave', '请假'),
        ('present', '正常'),
    )

    enum_activity_level = (
        ('none', '未参加'),
        ('low', '班级'),
        ('mid', '院级'),
        ('high', '校级'),
    )

    enum_discipline_level = (
        ('none', '正常'),
        ('low', '轻度违纪'),
        ('mid', '中度违纪'),
        ('high', '严重违纪'),
    )

    activity = models.ForeignKey(Activity, models.CASCADE)
    student = models.ForeignKey(Student, models.CASCADE)
    status = models.CharField('考勤状态', choices = enum_student_status, max_length = 20, default = 'present')
    level = models.CharField('参与情况', choices = enum_activity_level, max_length = 20, default = 'none')
    discipline = models.CharField('违纪情况', choices = enum_discipline_level, max_length = 20, default = 'none')

class SummaryCount(models.Model):
    student = models.ForeignKey(Student, models.CASCADE)
    year = models.IntegerField("年", default = datetime.now().year)
    month = models.IntegerField("月", default = datetime.now().month)
    absent_count = models.IntegerField("缺勤次数", default = 0)
    late_count = models.IntegerField("迟到次数", default = 0)
    leave_count = models.IntegerField("请假次数", default = 0)
    low_count = models.IntegerField("一般活动次数", default = 0)
    mid_count = models.IntegerField("中级活动次数", default = 0)
    high_count = models.IntegerField("重要活动次数", default = 0)
    discipline_low_count = models.IntegerField("轻度违纪次数", default = 0)
    discipline_mid_count = models.IntegerField("中度违纪次数", default = 0)
    discipline_high_count = models.IntegerField("严重违纪次数", default = 0)

    def score(self):
        total = 60 \
            + self.absent_count                 * (-2) \
            + self.late_count                   * (-1) \
            + self.leave_count                  * ( 0) \
            + self.low_count                    * ( 1) \
            + self.mid_count                    * ( 3) \
            + self.high_count                   * ( 5) \
            + self.discipline_low_count         * (-5) \
            + self.discipline_mid_count         * (-8) \
            + self.discipline_high_count        * (-12)

        return 0 if total < 0 else total

# triggers
def count_summary_with_instance(instance):
    today = datetime.now()
    student = instance.student
    atype = instance.activity.activity_type
    sc = None
    try:
        scs = student.summarycount_set.all()
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
        else:
            sc = SummaryCount(student = student, year = today.year, month = today.month)

            reports = student.report_set.filter(activity__time__year = today.year, activity__time__month = today.month)
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

    except Exception as e:
        pass

@receiver(post_save, sender = Report)
def report_finished_saving(sender, instance, **kwargs):
    count_summary_with_instance(instance)

@receiver(post_delete, sender = Report)
def report_finished_removing(sender, instance, **kwargs):
    count_summary_with_instance(instance)

@receiver(post_save, sender = Activity)
def activity_finished_saving(sender, instance, **kwargs):
    today = datetime.now()
    students = instance.inclass.student_set.all()

    for student in students:
        sc = None
        try:
            sc = student.summarycount_set.get(year = today.year, month = today.month)
        except Exception as e:
            pass

        if sc is None:
            sc = SummaryCount(student = student, year = today.year, month = today.month)
            sc.save()
