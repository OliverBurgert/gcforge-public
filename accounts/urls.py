from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("settings/add-account/",             views.add_account,            name="add_account"),
    path("settings/edit-account/",            views.edit_account,           name="edit_account"),
    path("settings/delete-account/",          views.delete_account,         name="delete_account"),
    path("settings/set-default-account/",     views.set_default_account,    name="set_default_account"),
    path("settings/login-account/",           views.login_account,          name="login_account"),
    path("account/oauth/start/",              views.oauth_start,            name="oauth_start"),
    path("account/oauth/callback/",           views.oauth_callback,         name="oauth_callback"),
    path("account/validate-oauth/",           views.account_validate_oauth, name="account_validate_oauth"),
    path("account/validate-gc/",             views.account_validate_gc,    name="account_validate_gc"),
    path("account/test-password/",            views.account_test_password,  name="account_test_password"),
    path("settings/save-platform-keys/",      views.save_platform_keys,     name="save_platform_keys"),
]
