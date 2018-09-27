from django.contrib import admin
from django.urls import path

from django.conf.urls import url
from manage import views as manage_views

urlpatterns = [
    #path('admin/', admin.site.urls),

    url(r'^$', manage_views.index, name='index'), # 首页
    url(r'^index.html$', manage_views.index, name='index'), # 首页
    url(r'^login.html$', manage_views.user_login, name='login'), # 登录
    url(r'^logout.html$', manage_views.user_logout, name='logout'), # 注销
    url(r'^class.html$', manage_views.class_info, name='classinfo'), # 班级信息
    url(r'^update_class_password.html$', manage_views.update_class, name='updateclass'), # 修改班级密码
    url(r'^create_activity.html$', manage_views.create_activity, name='createactivity'), # 创建活动
    url(r'^save_activity.html$', manage_views.save_activity, name='saveactivity'), # 保存活动
    url(r'^review_activity.html$', manage_views.review_activity, name='reviewactivity'), # 审核活动
    url(r'^release_activity.html$', manage_views.release_activity, name='releaseactivity'), # 发布活动
]

