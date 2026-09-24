from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('manage', '0007_average_decimal_places')]

    operations = [
        migrations.AlterField(
            model_name='activity', name='name',
            field=models.CharField('活动名称', max_length=64),
        ),
        migrations.AddField(
            model_name='activity', name='details',
            field=models.TextField('详情', blank=True, default='', max_length=500),
        ),
    ]
