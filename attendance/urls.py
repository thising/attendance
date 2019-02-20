"""attendance URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/2.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include

from django.conf.urls import url
from manage import views as manage_views

urlpatterns = [
    path('admin/', admin.site.urls),

    path('', include('manage.urls')),

#    url(r'^$', manage_views.index, name='index'), # 首页
#    url(r'^index.html$', manage_views.index, name='index'), # 首页
#    url(r'^login.html$', manage_views.user_login, name='login'), # 登录
#    url(r'^logout.html$', manage_views.user_logout, name='logout'), # 注销
#    url(r'^class.html$', manage_views.class_info, name='classinfo'), # 班级信息
#    url(r'^update_class_password.html$', manage_views.update_class, name='updateclass'), # 修改班级密码
#    url(r'^create_activity.html$', manage_views.create_activity, name='createactivity'), # 创建活动
#    url(r'^save_activity.html$', manage_views.save_activity, name='saveactivity'), # 保存活动
#    url(r'^review_activity.html$', manage_views.review_activity, name='reviewactivity'), # 审核活动
#    url(r'^release_activity.html$', manage_views.release_activity, name='releaseactivity'), # 发布活动
]
