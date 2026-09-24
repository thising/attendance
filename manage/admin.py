from django.contrib import admin
from .models import (Class, Student, Activity, Report, SummaryCount, AuditEvent,
                     ScoringPolicyVersion, RosterVersion, ClassTerm, CommitteeAccount)


class ReadOnlyBusinessAdmin(admin.ModelAdmin):
    """Business mutations must pass the same scoped services as the workspace."""
    actions = None

    def has_add_permission(self, request):return False
    def has_change_permission(self, request, obj=None):return False
    def has_delete_permission(self, request, obj=None):return False
    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser and request.user.is_active

    def get_exclude(self, request, obj=None):
        if self.model is Class:return ['managecode','sharecode','grant_version']
        if self.model is CommitteeAccount:return ['password','credential_ciphertext']
        return super().get_exclude(request,obj)


for model in (Class,Student,Activity,Report,SummaryCount,AuditEvent,ScoringPolicyVersion,RosterVersion,ClassTerm,CommitteeAccount):
    admin.site.register(model,ReadOnlyBusinessAdmin)
admin.site.site_header='笃行 · 只读业务后台'
admin.site.site_title='笃行'
