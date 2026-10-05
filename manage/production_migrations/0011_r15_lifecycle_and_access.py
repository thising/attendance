from importlib import import_module

base = import_module('manage.migrations.0010_r15_lifecycle_and_access')


class Migration(base.Migration):
    dependencies = [('manage', '0010_current_class_report')]
