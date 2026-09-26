from django.db import IntegrityError
from manage.models import Class, Student, RosterVersion
from . import calendar
from .access import get_class, actor_for, owner_actor, require_ready, validate_class_password
from .errors import BusinessError
from .rules import ensure_default_policy
from .report_snapshots import mark_report_dirty
from .scoring import roster_for
from .writes import atomic_write, once, event, require_revision


def student_input(data):
    if not isinstance(data,dict):raise BusinessError('invalid_student','学生资料无效。')
    number,name=data.get('number'),data.get('name')
    sex=data.get('sex','male')
    if not isinstance(number,str) or not isinstance(name,str) or not 1<=len(number.strip())<=20 or not 1<=len(name.strip())<=20:
        raise BusinessError('invalid_student','学号与姓名均需填写，最多20字。')
    if sex not in ('male','female'):raise BusinessError('invalid_student','性别值无效。')
    return {'number':number.strip(),'name':name.strip(),'sex':sex}


@atomic_write
def create_class(request,payload):
    actor=owner_actor(request)
    current=calendar.writable_term(payload.get('term_key'))
    def apply():
        name=payload.get('name')
        if not isinstance(name,str) or not 1<=len(name.strip())<=30:
            raise BusinessError('invalid_class_name','班级名称需填写，最多30字。')
        ensure_default_policy(actor.user_id)
        classroom=Class(classname=name.strip(),owner_id=actor.user_id,started_on=calendar.business_today())
        classroom.save()
        event(actor,classroom,'class_created','创建班级')
        mark_report_dirty(classroom)
        return {'id':classroom.pk,'code':classroom.code,'revision':1,'url':f'/classes/{classroom.code}/roster/'}
    return once(actor,None,f'class:create:{actor.user_id}',payload,apply)


@atomic_write
def change_roster(request,code,payload):
    classroom=get_class(code)
    actor=actor_for(request,classroom,owner_only=True)
    action=payload.get('action')
    require_ready(classroom)
    current=calendar.writable_term(payload.get('term_key'))
    def apply():
        require_revision(classroom.revision,payload.get('revision'))
        if action=='archive':
            classroom.archived=True
            classroom.revision+=1
            classroom.save(update_fields=['archived','revision'])
            event(actor,classroom,'class_archived','归档班级，保留名单与历史记录',revision=classroom.revision)
            mark_report_dirty(classroom)
            return {'revision':classroom.revision,'url':f'/classes/{code}/'}
        if action in ('preview_add','bulk_add'):
            text=payload.get('students_text','')
            if not isinstance(text,str):raise BusinessError('invalid_roster','批量名单格式无效。')
            rows=[]
            seen=set()
            for line_no,line in enumerate(text.splitlines(),1):
                if not line.strip():continue
                columns=[value.strip() for value in (line.split('\t') if '\t' in line else line.split('|'))]
                if len(columns)!=3:
                    raise BusinessError('invalid_roster','每行需为 学号|姓名|男/女。',details={'line':line_no})
                number,name,sex=columns
                try:fields=student_input({'number':number,'name':name,'sex':{'男':'male','女':'female'}.get(sex,sex)})
                except BusinessError as exc:
                    exc.details['line']=line_no
                    raise
                if number in seen:raise BusinessError('duplicate_student_number','批量名单内有重复学号。',409,{'line':line_no})
                seen.add(number)
                rows.append(fields)
            if not 1<=len(rows)<=1000:raise BusinessError('invalid_roster','请填写1–1000名学生。')
            existing={s.number:s for s in Student.objects.filter(inclass=classroom,number__in=seen)}
            active_ids={row['id'] for row in roster_for(classroom,current)}
            duplicate_line=next((index for index,row in enumerate(rows,1)
                if row['number'] in existing and existing[row['number']].pk in active_ids),None)
            if duplicate_line is not None:
                raise BusinessError('duplicate_student_number','批量名单中有学号已存在于本班当前名单，请核对后重试。',409,
                                    {'line':duplicate_line})
            reactivate_count=len(existing)
            if action=='preview_add':return {'students':rows,'count':len(rows),'reactivate_count':reactivate_count}
            restored=[]
            for fields in rows:
                student=existing.get(fields['number'])
                if student:
                    student.name=fields['name'];student.sex=fields['sex'];student.active=True;student.revision+=1
                    restored.append(student)
            if restored:Student.objects.bulk_update(restored,['name','sex','active','revision'])
            new_rows=[fields for fields in rows if fields['number'] not in existing]
            try:created=Student.objects.bulk_create([Student(inclass=classroom,**fields) for fields in new_rows])
            except IntegrityError:
                raise BusinessError('duplicate_student_number','名单提交期间出现重复学号，请刷新后重试。',409)
            added=restored+created
            versions={v.student_id:v for v in RosterVersion.objects.filter(
                student_id__in=[s.pk for s in added],effective_term_start=current.start)}
            version_updates=[];version_creates=[]
            for student in added:
                version=versions.get(student.pk)
                if version:
                    version.inclass=classroom;version.number=student.number;version.name=student.name
                    version.sex=student.sex;version.active=True;version_updates.append(version)
                else:version_creates.append(RosterVersion(student=student,inclass=classroom,
                    effective_term_start=current.start,number=student.number,name=student.name,sex=student.sex,active=True))
            if version_updates:RosterVersion.objects.bulk_update(version_updates,['inclass','number','name','sex','active'])
            if version_creates:RosterVersion.objects.bulk_create(version_creates)
            classroom.revision+=1
            classroom.save(update_fields=['revision'])
            event(actor,classroom,'roster_add',f'批量新增{len(added)}名学生，计入当前学期全部已进入月份',len(added),revision=classroom.revision)
            mark_report_dirty(classroom)
            return {'count':len(added),'reactivate_count':reactivate_count,
                    'revision':classroom.revision,'url':f'/classes/{code}/roster/'}
        if action not in ('add','edit','remove'):
            raise BusinessError('invalid_action','不支持此名单操作。')
        data=payload.get('student',{})
        student=None
        if action!='add':
            if not isinstance(data,dict) or type(data.get('id')) is not int:
                raise BusinessError('invalid_student','学生编号无效。')
            try:student=Student.objects.get(pk=data['id'],inclass=classroom)
            except Student.DoesNotExist:raise BusinessError('not_found','学生不存在或不属于本班。',404)
        if action=='remove':
            if not student.active:raise BusinessError('student_inactive','该学生已移出当前名单。',409)
            student.active=False
            student.revision+=1
            student.save(update_fields=['active','revision'])
        else:
            fields=student_input(data)
            duplicate=Student.objects.filter(inclass=classroom,number=fields['number'])
            if student:duplicate=duplicate.exclude(pk=student.pk)
            duplicate_student=duplicate.first()
            if duplicate_student:
                active_ids={row['id'] for row in roster_for(classroom,current)}
                if action!='add' or duplicate_student.pk in active_ids:
                    raise BusinessError('duplicate_student_number','本班已有此学号，请核对名单。',409)
                student=duplicate_student
            if student:
                for k,v in fields.items():setattr(student,k,v)
                student.active=True
                student.revision+=1
                student.save()
            else:student=Student.objects.create(inclass=classroom,**fields)
        RosterVersion.objects.update_or_create(student=student,effective_term_start=current.start,
            defaults={'inclass':classroom,'number':student.number,'name':student.name,'sex':student.sex,'active':student.active})
        classroom.revision+=1
        classroom.save(update_fields=['revision'])
        descriptions={'add':'新增1名学生，计入当前学期全部已进入月份','edit':'修改1名学生的当前学期资料','remove':'将1名学生移出当前名单，保留历史资料'}
        event(actor,classroom,'roster_'+action,descriptions[action],1,student.pk,classroom.revision)
        mark_report_dirty(classroom)
        return {'id':student.pk,'revision':classroom.revision,'url':f'/classes/{code}/roster/'}
    if action=='preview_add':return apply()
    return once(actor,classroom,f'roster:{classroom.pk}:{action}',payload,apply)
