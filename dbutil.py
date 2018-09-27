# -*- coding: utf-8 -*-
"""
__title__ = 'dbutil.py'
__author__ = 'kyle'
__mtime__ = '2018/09/23'
"""

import getopt
import sys

# 1. set envirment
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "attendance.settings")

# 2. initialize django
import django
django.setup()

# 3. import models
from django.contrib.auth.models import User
from manage.models import *

USER = "user"
CLASS = "class"
STUDENT = "student"

def TAG(t):
    return "[%s]" % t

# 4. process your files
def process_buffer(buf, t):
    print("Processing %s(%d)..." % (t, len(buf)))
    
    if t == USER:
        process_user(buf)
    elif t == CLASS:
        process_class(buf)
    elif t == STUDENT:
        process_students(buf)

def process_user(buf):
    username = None
    password = None
    for line in buf:
        componts = line.split('=')
        if componts[0].strip() == "username":
            username = componts[1].strip()
        elif componts[0].strip() == "password":
            password = componts[1].strip()
    user = User.objects.get_or_create(username = username)
    user.password = password
    user.save()

def process_class(buf):
    user = None
    for line in buf:
        if not user:
            componts = line.split("username=")
            if len(componts) > 1:
                user = User.objects.get(username = componts[1].strip())
        else:
            componts = line.split("|")
            classname = componts[0]
            password = componts[1]
            c, isnew = Class.objects.get_or_create(classname = classname, owner = user)
            print(c)
            c.managecode = password
            c.save()

def process_students(buf):
    user = None
    myclass = None
    for line in buf:
        if not user:
            componts = line.split("username=")
            if len(componts) > 1:
                user = User.objects.get(username = componts[1].strip())
        else:
            if not myclass:
                componts = line.split("classname=")
                if len(componts) > 1:
                    myclass = Class.objects.get(classname = componts[1].strip(), owner = user)
            else:
                componts = line.split("|")
                number = componts[0]
                name = componts[1]
                sex = "male" if componts[2] == "男" else "female"
                s, isnew = Student.objects.get_or_create(inclass = myclass, number = number)
                s.name = name
                s.sex = sex
                print(s)
                s.save()

def import_file_to_db(path):
    with open(path, 'r') as f:
        buf = []
        t = None
        for line in f:
            if line.strip() == TAG(USER):
                t = USER
                if len(buf) > 0:
                        process_buffer(buf, t)
                        buf = []
                        t = None
            elif line.strip() == TAG(CLASS):
                t = CLASS
                if len(buf) > 0:
                        process_buffer(buf, t)
                        buf = []
                        t = None
            elif line.strip() == TAG(STUDENT):
                t = STUDENT
                if len(buf) > 0:
                        process_buffer(buf, t)
                        buf = []
                        t = None
            elif t:
                buf += [line]

        if len(buf) > 0:
            process_buffer(buf, t)

if __name__ == '__main__':
    opts, args = getopt.getopt(sys.argv[1:], 'hf:', ['file=', 'help'])

    path = None
    for key, value in opts:
        if key in ['-h', '--help']:
            print('Usage: tools -f <file to import>')
            sys.exit(0)
        if key in ['-f', '--file']:
            path = value

    if path:
        import_file_to_db(path)
