from django.urls import path, re_path
from . import views

urlpatterns = [
    path('',views.index,name='index'),
    path('login/',views.user_login,name='login'),
    path('logout/',views.user_logout,name='logout'),
    path('classes/<str:code>/',views.class_info,name='classinfo'),
    path('classes/<str:code>/august/',views.august_records,name='augustrecords'),
    path('classes/<str:code>/authorize/',views.class_authorize,name='classauthorize'),
    path('classes/<str:code>/records/new/',views.record_page,name='createactivity'),
    path('classes/<str:code>/records/<int:record_id>/',views.record_page,name='reviewactivity'),
    path('classes/<str:code>/students/<int:student_id>/',views.student_detail,name='studentdetail'),
    path('classes/<str:code>/roster/',views.roster_page,name='students'),
    path('classes/<str:code>/roster/import/',views.import_students,name='importstudents'),
    path('classes/<str:code>/management/',views.class_management,name='classmanagement'),
    path('classes/<str:code>/committee/',views.committee_accounts,name='committeeaccounts'),
    path('classes/<str:code>/committee/credentials/',views.committee_credentials,name='committeecredentials'),
    path('classes/<str:code>/events/',views.events_page,name='classevents'),
    path('classes/<str:code>/public-report/',views.public_report_settings,name='publicreportsettings'),
    path('report/<str:token>/',views.public_report,name='publicreport'),
    path('rules/',views.rules_page,name='rules'),
    re_path(r'^[-a-z_]+\.html$',views.legacy_endpoint,name='legacyendpoint'),
]
