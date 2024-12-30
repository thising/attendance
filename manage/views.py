# -*- coding:utf-8 -*-

from django.shortcuts import render
from django.shortcuts import redirect
from django.contrib.auth import authenticate, login, logout
from django.http import HttpResponse

from datetime import datetime
from manage.models import *

# import the logging library
import logging

# Get an instance of a logger
logger = logging.getLogger(__name__)

# Create your views here.

def index(request):
    class_list = None
    to_show = None
    if request.user.is_authenticated:
        summary_tm = []
        class_list = Class.objects.filter(owner = request.user)
        to_show = []
        line = []
        for item in class_list:
            summary_tm += [{"class" : item.id, "summary" : [s.summary_term() for s in item.student_set.all()]}]
            line += [item]
            if len(line) == 3:
                to_show += [line]
                line = []
        if len(line) > 0:
            to_show += [line]
        return render(request, 'index.html', {"class_list": to_show, "class_count": 0 if class_list is None else len(class_list), "summary_tm":summary_tm})
    return render(request, 'index.html')

def user_login(request):
    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        user = authenticate(username = username, password = password)
        if user is not None:
            login(request, user)
            logger.info("用户 <%r> 登录成功", username)
            return redirect("index.html")
        else:
            logger.info("用户 <%r> 登录失败", username)
            return render(request, "login.html", {"message": "用户名或密码错误"})
    else:
        return render(request, "login.html")

def user_logout(request):
    logout(request)
    return redirect("index.html")

def class_info(request):
    is_owner = False
    activities = None
    summary = []
    summary_last_month = []

    classcode = request.GET.get("classcode", "")
    current_class = Class.objects.get(sharecode = classcode)
    if current_class is not None:
        today = datetime.now()
        logger.info("用户 <%r> 访问班级 <%r>", request.user.username, current_class)
        if request.user.is_authenticated and current_class.owner == request.user:
            is_owner = True
            # activities = Activity.objects.filter(inclass = current_class, status = "preview")
            activities = current_class.activity_set.all()
            students = Student.objects.filter(inclass = current_class)
            for student in students:
                item = student.summary_current_month()
                if item:
                    summary += [item]
                item_lm = student.summary_last_month()
                if item_lm:
                    summary_last_month += [item_lm]

        activities_today = current_class.activity_set.filter(time__gt = datetime(today.year, today.month, today.day))

        return render(request, "class.html", {
                "class": current_class,
                "is_owner": is_owner,
                "activities": None if activities is None else activities[len(activities_today):80],
                "activities_today": activities_today,
                "summary": summary,
                "summary_lm": summary_last_month,
                "today": today,
            })    
    else:
        return redirect("index.html")

def student_term_reports(request):
    is_owner = False
    sid = request.GET.get("student", "")
    student = Student.objects.get(id = sid)
    if student is not None:
        if request.user.is_authenticated and student.inclass.owner == request.user:
            is_owner = True
            return render(request, "student_term_reports.html", {"name": student.name, "reports" : student.report_term()})
    return render(request, "student_term_reports.html", {"name": "数据错误", "reports" : [("数据查询失败", "数据查询失败", "数据查询失败")]})

def student_last_term_reports(request):
    is_owner = False
    sid = request.GET.get("student", "")
    student = Student.objects.get(id = sid)
    if student is not None:
        if request.user.is_authenticated and student.inclass.owner == request.user:
            is_owner = True
            return render(request, "student_term_reports.html", {"name": student.name, "reports" : student.report_term_last()})
    return render(request, "student_term_reports.html", {"name": "数据错误", "reports" : [("数据查询失败", "数据查询失败", "数据查询失败")]})

def update_class(request):
    if request.user.is_authenticated:
        classcode = request.POST.get("classcode", "")
        managecode = request.POST.get("managecode", "")
        current_class = Class.objects.get(sharecode = classcode)
        if current_class is not None and current_class.owner == request.user:
            logger.info("用户 <%r> 更改班级 <%r> 的管理密码", request.user.username, current_class)
            current_class.managecode = managecode
            current_class.save()

    return redirect("class.html?classcode=%s" % classcode)

def create_activity(request):
    classcode = request.POST.get("classcode", "")
    current_class = Class.objects.get(sharecode = classcode)
    if current_class is not None:
        managecode = request.POST.get("managecode", "")
        today = datetime.now()
        today_count = current_class.activity_set.all().filter(time__gt = datetime(today.year, today.month, today.day)).count()
        if (managecode == current_class.managecode and today_count < 30) or (request.user.is_authenticated and current_class.owner == request.user):
            return render(request, "create_activity.html", {
                    "students": current_class.student_set.all(),
                    "class": current_class,
                })
    return redirect("class.html?classcode=%s" % classcode)

def save_activity(request):
    # TODO: 2022-02-18 Kyle
    # 短时间多次提交的重复判断
    classcode = request.POST.get("classcode", "")
    current_class = Class.objects.get(sharecode = classcode)
    if current_class is not None:
        activity_type = request.POST.get("type", "")
        activity = Activity(
                activity_type = activity_type,
                name = request.POST.get("name", ""),
                inclass = current_class
            )
        logger.info("用户 <%r> 为班级 <%r> 创建活动 <%r>", request.user.username, current_class.classname, activity.name)
        activity.save()
        # students = Student.objects.filter(inclass = current_class)
        students = current_class.student_set.all()
        for student in students:
            report = None
            if activity_type == "class":
                status = request.POST.get("scid_%d" % student.id, None)
                if status and status != "present":
                    report = Report(
                            activity = activity,
                            student = student,
                            status = status
                        )
            elif activity_type == "activity":
                level = request.POST.get("said_%d" % student.id, None)
                if level and level != "none":
                    report = Report(
                            activity = activity,
                            student = student,
                            level = level
                        )
            else:
                discipline = request.POST.get("sdid_%d" % student.id, None)
                if discipline and discipline != "none":
                    report = Report(
                            activity = activity,
                            student = student,
                            discipline = discipline
                        )
                pass
            if report:
                report.save()

    return redirect("class.html?classcode=%s" % classcode)

def review_activity(request):
    classcode = request.POST.get("classcode", "")
    current_class = Class.objects.get(sharecode = classcode)

    aid = request.POST.get("aid", "")
    activity = Activity.objects.get(id = aid)

    is_owner = False
    if request.user.is_authenticated and current_class.owner == request.user:
            is_owner = True

    if current_class is not None and activity is not None:
        to_show = []
        students = current_class.student_set.all()
        for student in students:
            status = "present"
            level = "none"
            discipline = "none"
            report = None

            try:
                report = Report.objects.get(activity = activity, student = student)
            except Exception as e:
                report = None
            
            if report:
                if activity.activity_type == 'class':
                    status = report.status
                elif activity.activity_type == 'activity':
                    level = report.level
                else:
                    discipline = report.discipline

            to_show += [(
                    student.id,
                    student.number,
                    student.name,
                    student.get_sex_display(),
                    status,
                    level,
                    discipline
                )]

        return render(request, "review_activity.html", {
                "class": current_class,
                "is_owner": is_owner,
                "activity": activity,
                "is_class": activity.activity_type == 'class',
                "is_activity": activity.activity_type == 'activity',
                "is_discipline": activity.activity_type == 'discipline',
                "students": to_show,
            })
    return redirect("class.html?classcode=%s" % classcode)

def remove_activity(request):
    classcode = request.POST.get("classcode", "")
    current_class = Class.objects.get(sharecode = classcode)

    aid = request.POST.get("aid", "")
    activity = Activity.objects.get(id = aid)

    is_owner = False
    if request.user.is_authenticated and current_class.owner == request.user:
        is_owner = True

    if current_class is not None and activity is not None:
        logger.info("用户 <%r> 开始删除班级 <%r> 中的活动 <%r><%r> !!!", request.user.username, current_class.classname, aid, activity.name)

        reports = Report.objects.filter(activity = activity)
        for r in reports:
            r.delete()

        activity.delete()

    return redirect("class.html?classcode=%s" % classcode)

def release_activity(request):
    classcode = request.POST.get("classcode", "")
    current_class = Class.objects.get(sharecode = classcode)

    managecode = None
    is_owner = False
    if request.user.is_authenticated and current_class.owner == request.user:
        is_owner = True
    else:
        managecode = request.POST.get("managecode", "")

    aid = request.POST.get("aid", "")
    activity = Activity.objects.get(id = aid)

    if is_owner or managecode == current_class.managecode:
        logger.info("用户 <%r> 为班级 <%r> 修改活动 <%r>", request.user.username, current_class.classname, activity.name)
        if current_class is not None and activity is not None:
            activity.name = request.POST.get("name", "")
            if is_owner:
                activity.status = "release"
            activity.save()

            students = current_class.student_set.all()
            for student in students:
                report = None
                
                try:
                    report = Report.objects.get(activity = activity, student = student)
                except Exception as e:
                    report = None

                if activity.activity_type == "class":
                    status = request.POST.get("scid_%d" % student.id, None)
                    if status:
                        if report:
                            if status != "present":
                                report.status = status
                            else:
                                report.delete()
                                report = None
                        elif status != "present":
                            report = Report(
                                activity = activity,
                                student = student,
                                status = status
                                )
                        else:
                            pass
                elif activity.activity_type == "activity":
                    level = request.POST.get("said_%d" % student.id, None)
                    if level:
                        if report:
                            if level != "none":
                                report.level = level
                            else:
                                report.delete()
                                report = None
                        elif level != "none":
                            report = Report(
                                activity = activity,
                                student = student,
                                level = level
                                )
                        else:
                            pass
                else:
                    discipline = request.POST.get("sdid_%d" % student.id, None)
                    if discipline:
                        if report:
                            if discipline != "none":
                                report.discipline = discipline
                            else:
                                report.delete()
                                report = None
                        elif discipline != "none":
                            report = Report(
                                activity = activity,
                                student = student,
                                discipline = discipline
                                )
                        else:
                            pass

                if report:
                    report.save()

        return redirect("class.html?classcode=%s" % classcode)

    if current_class is not None and activity is not None:
        to_show = []
        students = current_class.student_set.all()
        for student in students:
            status = "present"
            level = "none"
            discipline = "none"
            report = None
            
            try:
                report = Report.objects.get(activity = activity, student = student)
            except Exception as e:
                report = None

            if report:
                if activity.activity_type == "class":
                    status = report.status
                elif activity.activity_type == "activity":
                    level = report.level
                else:
                    discipline = report.discipline

            to_show += [(
                    student.id,
                    student.number,
                    student.name,
                    student.get_sex_display(),
                    status,
                    level,
                    discipline
                )]

        return render(request, "review_activity.html", {
                "class": current_class,
                "is_owner": is_owner,
                "activity": activity,
                "is_class": activity.activity_type == "class",
                "is_activity": activity.activity_type == "activity",
                "is_discipline": activity.activity_type == "discipline",
                "students": to_show,
                "message": "保存失败",
            })

def clear_history(request):
    classcode = request.POST.get("classcode", "")
    current_class = Class.objects.get(sharecode = classcode)
    if current_class is not None:
        if request.user.is_authenticated and current_class.owner == request.user:
            logger.info("用户 <%r> 开始在班级 <%r> 中清除历史!!!", request.user.username, current_class.classname)
            students = Student.objects.filter(inclass = current_class)
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
                # s.delete()

            # remove activities
            records = Activity.objects.filter(inclass = current_class)
            for i in records:
                i.delete()

    return redirect("class.html?classcode=%s" % classcode)

def remove_class(request):
    classcode = request.POST.get("classcode", "")
    current_class = Class.objects.get(sharecode = classcode)
    if current_class is not None:
        if request.user.is_authenticated and current_class.owner == request.user:
            logger.info("用户 <%r> 开始删除班级 <%r> !!!", request.user.username, current_class.classname)
            students = Student.objects.filter(inclass = current_class)
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
            records = Activity.objects.filter(inclass = current_class)
            for i in records:
                i.delete()

            # remove classes
            current_class.delete()

    return redirect("index.html")

def add_class(request):
    classname = request.POST.get("classname", "")
    try:
        inclass = Class.objects.get(classname = classname)
    except Exception as e:
        inclass = None
    
    if inclass is None:
        if request.user.is_authenticated:
            inclass = Class(classname = classname, managecode = "", owner = request.user)

            if inclass:
                logger.info("用户 <%r> 开始创建班级 <%r> !!!", request.user.username, inclass.classname)
                inclass.save()

    return redirect("index.html")

def last_term_summary(request):
    class_list = None
    if request.user.is_authenticated:
        summary_tm = []
        class_list = Class.objects.filter(owner = request.user)
        for item in class_list:
            summary_tm += [{"class" : item.id, "summary" : [s.summary_term_last() for s in item.student_set.all()]}]
        return render(request, 'summary-last-term.html', {"summary_tm":summary_tm})

    return render(request, 'summary-last-term.html')

def students(request):
    is_owner = False
    students = None
    classcode = request.GET.get("classcode", "")
    current_class = Class.objects.get(sharecode = classcode)
    if current_class is not None:
        if request.user.is_authenticated and current_class.owner == request.user:
            is_owner = True
            students = Student.objects.filter(inclass = current_class)

        return render(request, "students.html", {
                "class": current_class,
                "is_owner": is_owner,
                "students": students,
            })    
    else:
        return redirect("index.html")

def remove_students(request):
    classcode = request.POST.get("classcode", "")
    current_class = Class.objects.get(sharecode = classcode)
    if current_class is not None:
        if request.user.is_authenticated and current_class.owner == request.user:
            logger.info("用户 <%r> 开始在班级 <%r> 中删除学生:", request.user.username, current_class.classname)
            students = current_class.student_set.all()
            for student in students:
                status = request.POST.get("sid_%d" % student.id, None)
                if status:
                    logger.info("--> Remove %r", student)
                    records = student.report_set.all()
                    for i in records:
                        i.delete()

                    records = student.summarycount_set.all()
                    for i in records:
                        i.delete()
                    
                    student.delete()

    return redirect("students.html?classcode=%s" % classcode)

def add_students(request):
    classcode = request.POST.get("classcode", "")
    current_class = Class.objects.get(sharecode = classcode)
    if current_class is not None:
        if request.user.is_authenticated and current_class.owner == request.user:
            logger.info("用户 <%r> 开始在班级 <%r> 中添加学生:", request.user.username, current_class.classname)
            buf = request.POST.get("students-text", "")
            records = buf.split("\n")
            for i in records:
                cols = i.strip().split("|")
                if len(cols) >= 3:
                    number = cols[0].strip()
                    name = cols[1].strip()
                    sex = cols[2].strip()
                    sex = 'male' if (sex == '男' or sex == 'male') else 'female'
                    
                    try:
                        stu = Student.objects.get(inclass = current_class, number = number)
                    except Exception as e:
                        stu = None
                    
                    if stu is None:
                        try:
                            stu = Student(inclass = current_class, number = number, name = name, sex = sex)
                            stu.save()
                            logger.info("--> Added: %r, %r", number, name)
                        except Exception as e:
                            logger.error(e)
                        else:
                            pass
                        finally:
                            pass
                    else:
                        logger.info("xxx Ignore duplicated: %r, %r", number, name)

    return redirect("students.html?classcode=%s" % classcode)