"""Read-only release preflight; never invent an old archived class's ending."""
from django.core.management.base import BaseCommand, CommandError
from manage.models import Class
from manage.services.scoring import validate_ended_archive
from manage.services.errors import BusinessError


class Command(BaseCommand):
    help = '只读检查结束管理班级是否具备可发布的冻结依据，不修改业务数据'

    def handle(self, *args, **options):
        unresolved = []
        for classroom in Class.objects.filter(archived=True).iterator():
            try:
                validate_ended_archive(classroom)
            except BusinessError:
                unresolved.append(classroom.pk)
        if unresolved:
            raise CommandError('结束管理依据待核实，禁止推算回填；班级 ID：' + ','.join(map(str, unresolved)))
        self.stdout.write('结束管理发布预检通过；未修改数据库。')
