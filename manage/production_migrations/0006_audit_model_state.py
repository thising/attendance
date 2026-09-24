from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [('manage', '0005_configurable_scores_and_account_credentials')]
    operations = [migrations.SeparateDatabaseAndState(state_operations=[migrations.AlterField(model_name='summarycount',name='year',field=models.IntegerField(default=2020,verbose_name='年'))])]
