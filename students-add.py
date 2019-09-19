# -*- coding:utf-8 -*-

from django.db import models
from manage.models import *

with open("./students.data", "r") as f:
    # data format:
    # class|number|name|sex
    buf = f.read()
    records = buf.split("\n")
    for i in records:
        cols = i.split("|")
        if len(cols) == 4:
            classname = cols[0]
            number = cols[1]
            name = cols[2]
            sex = cols[3]
            try:
                inclass = Class.objects.get(classname = classname)
            except Exception as e:
                inclass = None
            
            if inclass is None:
                inclass = Class(classname = classname, managecode = "", owner = User.objects.get(username = "karen"))

            if inclass:
                inclass.save()
                try:
                    stu = Student.objects.get(inclass = inclass, number = number)
                except Exception as e:
                    stu = None
                
                if stu is None:
                    try:
                        stu = Student(inclass = inclass, number = number, name = name, sex = sex)
                        stu.save()
                        print("创建成功:", number, name)
                    except Exception as e:
                        print(e)
                    else:
                        pass
                    finally:
                        pass
                else:
                    print("***重复学生:", number, name)
            else:
                print("***班级创建失败:", classname)
