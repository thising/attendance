"""Create a local-only, synthetic demo in an empty dedicated database."""
import json
import secrets
import uuid
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware
from manage.models import Class
from manage.services.roster import create_class, change_roster
from manage.services.records import save_record
from manage.services.committee import manage_accounts


class Command(BaseCommand):
    help='在空的 .local/development.sqlite3 中创建完全虚构的本机演示数据。'

    def handle(self,*args,**options):
        expected=(settings.BASE_DIR/'.local/development.sqlite3').resolve()
        if Path(settings.DATABASES['default']['NAME']).resolve()!=expected:
            raise CommandError('演示数据仅允许写入项目 .local/development.sqlite3。')
        User=get_user_model()
        if User.objects.exists() or Class.objects.exists():raise CommandError('数据库非空，拒绝覆盖。')
        secret=secrets.token_urlsafe(16)
        user=User.objects.create_user('demo-owner',password=secret)
        request=RequestFactory().post('/')
        SessionMiddleware(lambda r:None).process_request(request)
        request.user=user
        def key():return str(uuid.uuid4())
        names=['林沐','陈知夏','许亦','沈聿','唐宁','周予','叶安','江白','顾言','宋禾','陆遥','程星']
        classes=[]
        with patch('manage.services.calendar.business_today',return_value=date(2026,4,15)), patch('django.utils.timezone.now',return_value=datetime(2026,4,15,10)):
            for index,name in enumerate(['视觉传达设计 2401','环境设计 2402']):
                result=create_class(request,{'name':name,'term_key':'2026-spring','submission_id':key()})
                c=Class.objects.get(pk=result['id'])
                ids=[]
                for n in range(6):
                    c.refresh_from_db()
                    row=change_roster(request,c.code,{'action':'add','student':{'number':f'240{index+1}{n+1:02d}','name':names[index*6+n]},
                        'term_key':'2026-spring','submission_id':key(),'revision':c.revision})
                    ids.append(row['id'])
                c.refresh_from_db()
                save_record(request,c.code,{'submission_id':key(),'term_key':'2026-spring','revision':0,'roster_revision':c.revision,
                    'record':{'kind':'class','name':'专业课 · 春季示例','date':'2026-04-15','students':[{'id':sid,'value':'late' if n==0 else 'normal'} for n,sid in enumerate(ids)]}})
                classes.append((c,ids))
        with patch('manage.services.calendar.business_today',return_value=date(2026,9,22)), patch('django.utils.timezone.now',return_value=datetime(2026,9,22,9)):
            for c,ids in classes:
                for kind,name,values in [('class','专业课 · 上午',['normal','late','normal','absent','normal','leave']),
                    ('activity','学院展览志愿服务',['mid','normal','low','normal','normal','normal'])]:
                    save_record(request,c.code,{'submission_id':key(),'term_key':'2026-autumn','revision':0,'roster_revision':c.revision,
                        'record':{'kind':kind,'name':name,'date':'2026-09-22','students':[{'id':sid,'value':values[n]} for n,sid in enumerate(ids)]}})
        committee=[]
        for index,(c,_) in enumerate(classes,1):
            username=f'demo.committee.{index}'
            account_password=secrets.token_urlsafe(16)
            result=manage_accounts(request,c.code,{'action':'create','username':username,'display_name':f'演示班委{index}',
                'password':account_password,'submission_id':key()})
            committee.append({'class_id':c.pk,'username':result['receipt']['username'],'password':account_password})
        access=settings.BASE_DIR/'.local/demo-access.json'
        access.write_text(json.dumps({'username':'demo-owner','password':secret,'classes':[c.code for c,_ in classes],'committee_accounts':committee},ensure_ascii=False))
        access.chmod(0o600)
        self.stdout.write(f'合成演示已创建；本机访问资料：{access}')
