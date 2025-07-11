# ruff:noqa: PLR2004
import random
import re
import string
from datetime import datetime
from unittest.mock import patch

import django_comments
from django.contrib.auth.models import AnonymousUser, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.contrib.sites.models import Site
from django.http import HttpRequest
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django_comments.models import CommentFlag
from django_comments.views import comments

from django_comments_xtd import signals, signed, views
from django_comments_xtd.conf import settings
from django_comments_xtd.models import (
    DISLIKEDIT_FLAG,
    LIKEDIT_FLAG,
    TmpXtdComment,
    XtdComment,
)
from django_comments_xtd.tests.models import Article, Diary, Quote
from django_comments_xtd.views import (
    on_comment_was_posted,
    on_comment_will_be_posted,
)

request_factory = RequestFactory()


def post_article_comment(data, article, auth_user=None):
    request = request_factory.post(
        reverse(
            "article-detail",
            kwargs={
                "year": article.publish.year,
                "month": article.publish.month,
                "day": article.publish.day,
                "slug": article.slug,
            },
        ),
        data=data,
        follow=True,
    )
    if auth_user:
        request.user = auth_user
    else:
        request.user = AnonymousUser()
    request._dont_enforce_csrf_checks = True
    return comments.post_comment(request)


def post_quote_comment(data, quote, auth_user=None):
    request = request_factory.post(
        reverse(
            "quote-detail",
            kwargs={
                "year": quote.publish.year,
                "month": quote.publish.month,
                "day": quote.publish.day,
                "slug": quote.slug,
            },
        ),
        data=data,
        follow=True,
    )
    if auth_user:
        request.user = auth_user
    else:
        request.user = AnonymousUser()
    request._dont_enforce_csrf_checks = True
    return comments.post_comment(request)


def post_diary_comment(data, diary_entry, auth_user=None):
    request = request_factory.post(
        reverse(
            "diary-detail",
            kwargs={
                "year": diary_entry.publish.year,
                "month": diary_entry.publish.month,
                "day": diary_entry.publish.day,
            },
        ),
        data=data,
        follow=True,
    )
    if auth_user:
        request.user = auth_user
    else:
        request.user = AnonymousUser()
    request._dont_enforce_csrf_checks = True
    return comments.post_comment(request)


def confirm_comment_url(key, follow=True):
    request = request_factory.get(
        reverse("comments-xtd-confirm", kwargs={"key": key}), follow=follow
    )
    request.user = AnonymousUser()
    return views.confirm(request, key)


class ViewUtilitiesTest(TestCase):
    @patch("django_comments_xtd.views.settings.COMMENTS_APP")
    def test_on_comment_will_be_posted_returns_true_for_custom_comment_app(
        self, mock_setting
    ):
        mock_setting.return_value = "custom_comment_app"
        comment = XtdComment()
        request = HttpRequest()
        self.assertTrue(
            on_comment_will_be_posted(
                comment=comment, request=request, sender=TmpXtdComment
            )
        )

    @patch("django_comments_xtd.views.settings.COMMENTS_APP")
    def test_on_comment_was_posted_returns_false_for_custom_comment_app(
        self, mock_setting
    ):
        mock_setting.return_value = "custom_comment_app"
        comment = XtdComment()
        request = HttpRequest()
        self.assertFalse(
            on_comment_was_posted(
                comment=comment, request=request, sender=TmpXtdComment
            )
        )


class DummyViewTestCase(TestCase):
    def setUp(self):
        self.user = AnonymousUser()

    def test_dummy_view_response(self):
        response = self.client.get(
            reverse(
                "diary-detail", kwargs={"year": 2022, "month": 10, "day": 4}
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"Got it")


class CommentViewsTest(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("bob", "bob@example.com", "pwd")
        self.site = Site.objects.get(pk=1)
        self.article = Article.objects.create(
            title="September",
            slug="september",
            body="What I did on September...",
        )
        self.comment = XtdComment.objects.create(
            content_object=self.article,
            site=self.site,
            comment="comment 1 to article",
        )
        self.diary = Diary.objects.create(body="What I did on September...")
        self.diary_comment = XtdComment.objects.create(
            content_object=self.diary,
            site=self.site,
            comment="comment 1 to diary",
        )

    def test_like_view(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("comments-xtd-like", args=[self.diary_comment.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm your opinion")
        self.assertContains(response, "Do you like this comment?")
        self.assertContains(response, self.diary.get_absolute_url())
        self.assertContains(
            response, "Please, confirm your opinion about the comment."
        )
        self.assertEqual(response.context["comment"], self.diary_comment)

    def test_like_view_contains_user_url_if_available(self):
        self.diary_comment.user_url = "https://example.com/user/me/"
        self.diary_comment.save()
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("comments-xtd-like", args=[self.diary_comment.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Do you like this comment?")
        self.assertContains(response, "https://example.com/user/me/")

    def test_like_view_already_liked(self):
        CommentFlag.objects.create(
            comment=self.diary_comment,
            user=self.user,
            flag=LIKEDIT_FLAG,
        )
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("comments-xtd-like", args=[self.diary_comment.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm your opinion")
        self.assertContains(
            response, "You liked this comment, do you want to change it?"
        )
        self.assertContains(response, self.diary.get_absolute_url())
        self.assertContains(
            response, "Please, confirm your opinion about the comment."
        )
        self.assertContains(
            response,
            'Click on the "withdraw" button if you want '
            "to withdraw your positive opinion on this comment.",
        )
        self.assertEqual(response.context["comment"], self.diary_comment)

    @patch("django_comments_xtd.views.get_app_model_options")
    def test_like_view_with_feedback_disabled(self, mock_get_app_model_options):
        mock_get_app_model_options.return_value = {"allow_feedback": False}
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("comments-xtd-like", args=[self.comment.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_like_done_view(self):
        response = self.client.get(reverse("comments-xtd-like-done"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your opinion is appreciated")
        self.assertContains(
            response, "Thanks for taking the time to participate."
        )

    def test_dislike_view(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("comments-xtd-dislike", args=[self.diary_comment.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm your opinion")
        self.assertContains(response, "Do you dislike this comment?")
        self.assertContains(response, self.diary.get_absolute_url())
        self.assertContains(
            response, "Please, confirm your opinion about the comment."
        )
        self.assertEqual(response.context["comment"], self.diary_comment)

    def test_dislike_view_contains_user_url_if_available(self):
        self.diary_comment.user_url = "https://example.com/user/me/"
        self.diary_comment.save()
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("comments-xtd-dislike", args=[self.diary_comment.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Do you dislike this comment?")
        self.assertContains(response, "https://example.com/user/me/")

    def test_dislike_view_already_disliked(self):
        CommentFlag.objects.create(
            comment=self.diary_comment,
            user=self.user,
            flag=DISLIKEDIT_FLAG,
        )
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("comments-xtd-dislike", args=[self.diary_comment.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm your opinion")
        self.assertContains(
            response, "You didn't like this comment, do you want to change it?"
        )
        self.assertContains(response, self.diary.get_absolute_url())
        self.assertContains(
            response, "Please, confirm your opinion about the comment."
        )
        self.assertContains(
            response,
            'Click on the "withdraw" button if you want '
            "to withdraw your negative opinion on this comment.",
        )
        self.assertEqual(response.context["comment"], self.diary_comment)

    @patch("django_comments_xtd.views.get_app_model_options")
    def test_dislike_view_with_feedback_disabled(
        self, mock_get_app_model_options
    ):
        mock_get_app_model_options.return_value = {"allow_feedback": False}
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("comments-xtd-dislike", args=[self.comment.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_dislike_done_view(self):
        response = self.client.get(reverse("comments-xtd-dislike-done"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You disliked the comment")
        self.assertContains(
            response, "Thanks for taking the time to participate."
        )

    def test_flag_view(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("comments-flag", args=[self.diary_comment.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Flag comment")
        self.assertContains(response, "Flag this comment?")
        self.assertContains(response, self.diary.get_absolute_url())
        self.assertContains(
            response,
            "Click on the flag button to mark the following comment "
            "as inappropriate.",
        )
        self.assertEqual(response.context["comment"], self.diary_comment)

    def test_flag_view_contains_user_url_if_available(self):
        self.diary_comment.user_url = "https://example.com/user/me/"
        self.diary_comment.save()
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("comments-flag", args=[self.diary_comment.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Flag comment")
        self.assertContains(response, "https://example.com/user/me/")

    @patch("django_comments_xtd.views.get_app_model_options")
    def test_flag_view_with_flagging_disabled(self, mock_get_app_model_options):
        mock_get_app_model_options.return_value = {"allow_flagging": False}
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("comments-flag", args=[self.comment.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_flag_done_view(self):
        response = self.client.get(
            reverse("comments-flag-done"), data={"c": self.comment.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Comment flagged")
        self.assertContains(response, "The comment has been flagged.")
        self.assertContains(response, self.article.get_absolute_url())
        self.assertContains(
            response,
            "Thank you for taking the time to improve the quality "
            "of discussion in our site.",
        )
        self.assertEqual(response.context["comment"], self.comment)

    def test_delete_view(self):
        self.user.user_permissions.add(
            Permission.objects.get_by_natural_key(
                codename="can_moderate",
                app_label="django_comments",
                model="comment",
            )
        )
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("comments-delete", kwargs={"comment_id": self.comment.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Remove comment")
        self.assertContains(response, "Remove this comment?")
        self.assertContains(response, self.comment.get_absolute_url())
        self.assertContains(response, "As a moderator you can delete comments.")
        self.assertContains(
            response,
            "Deleting a comment does not remove it from the site, "
            "only prevents your website from showing the text.",
        )
        self.assertEqual(response.context["comment"], self.comment)

    def test_delete_view_contains_user_url_if_available(self):
        self.comment.user_url = "https://example.com/user/me/"
        self.comment.save()
        self.user.user_permissions.add(
            Permission.objects.get_by_natural_key(
                codename="can_moderate",
                app_label="django_comments",
                model="comment",
            )
        )
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("comments-delete", kwargs={"comment_id": self.comment.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Remove comment")
        self.assertContains(response, "https://example.com/user/me/")

    def test_delete_done_view(self):
        response = self.client.get(
            reverse("comments-delete-done"), data={"c": self.comment.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Comment removed")
        self.assertContains(response, "The comment has been removed.")
        self.assertContains(response, self.article.get_absolute_url())
        self.assertContains(
            response,
            "Thank you for taking the time to improve the quality"
            " of discussion in our site.",
        )
        self.assertEqual(response.context["comment"], self.comment)

    def test_reply_form(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("comments-xtd-reply", kwargs={"cid": self.comment.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Comment reply")
        self.assertContains(response, "Reply to comment")
        self.assertContains(response, "Post your comment")
        self.assertEqual(response.context["comment"], self.comment)

    def test_reply_contains_user_url_if_available(self):
        self.comment.user_url = "https://example.com/user/me/"
        self.comment.save()
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("comments-xtd-reply", kwargs={"cid": self.comment.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Comment reply")
        self.assertContains(response, "https://example.com/user/me/")


class XtdCommentListViewTestCase(TestCase):
    def setUp(self) -> None:
        self.article_ct = ContentType.objects.get(
            app_label="tests", model="article"
        )
        self.site = Site.objects.get(pk=1)
        self.article = Article.objects.create(
            title="October", slug="october", body="What I did on October..."
        )

    def test_contains_comment(self):
        XtdComment.objects.create(
            content_object=self.article,
            site=self.site,
            comment="comment 1 to article",
            is_removed=False,
            is_public=True,
        )
        response = self.client.get(reverse("comments-xtd-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "comment 1 to article")

    def test_not_contains_removed_comments_or_marker(self):
        XtdComment.objects.create(
            content_object=self.article,
            site=self.site,
            comment="comment 1 to article",
            is_removed=True,
            is_public=True,
        )
        response = self.client.get(reverse("comments-xtd-list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "comment 1 to article")
        self.assertNotContains(response, "This comment has been removed.")


class OnCommentWasPostedTestCase(TestCase):
    def setUp(self):
        self.patcher = patch("django_comments_xtd.views.send_mail")
        self.mock_mailer = self.patcher.start()
        self.article = Article.objects.create(
            title="October", slug="october", body="What I did on October..."
        )
        self.quote = Quote.objects.create(
            title="October", slug="october", quote="Mas vale pájaro en mano..."
        )
        self.cm_form_to_article = django_comments.get_form()(self.article)
        self.cm_form_to_quote = django_comments.get_form()(self.quote)
        self.user = AnonymousUser()

    def tearDown(self):
        self.patcher.stop()

    def post_valid_data_to_article(self, auth_user=None, response_code=302):
        data = {
            "name": "Bob",
            "email": "bob@example.com",
            "followup": True,
            "reply_to": 0,
            "level": 1,
            "order": 1,
            "comment": "Es war einmal eine kleine...",
        }
        data.update(self.cm_form_to_article.initial)
        response = post_article_comment(data, self.article, auth_user)
        self.assertEqual(response.status_code, response_code)
        if response.status_code == 302:
            self.assertTrue(response.url.startswith("/comments/posted/?c="))

    def post_valid_data_to_quote(self, auth_user=None, response_code=302):
        data = {
            "name": "Bob",
            "email": "bob@example.com",
            "followup": True,
            "reply_to": 0,
            "level": 1,
            "order": 1,
            "comment": "Es war einmal eine kleine...",
        }
        data.update(self.cm_form_to_quote.initial)
        response = post_quote_comment(data, self.quote, auth_user)
        self.assertEqual(response.status_code, response_code)
        if response.status_code == 302:
            self.assertTrue(response.url.startswith("/comments/posted/?c="))

    def test_post_as_authenticated_user(self):
        self.user = User.objects.create_user("bob", "bob@example.com", "pwd")
        self.assertTrue(self.mock_mailer.call_count == 0)
        self.post_valid_data_to_article(auth_user=self.user)
        # no confirmation email sent as user is authenticated
        self.assertTrue(self.mock_mailer.call_count == 0)

    def test_confirmation_email_is_sent(self):
        self.assertTrue(self.mock_mailer.call_count == 0)
        self.post_valid_data_to_article()
        self.assertTrue(self.mock_mailer.call_count == 1)

    def test_post_as_visitor_when_only_users_can_post(self):
        self.assertTrue(self.mock_mailer.call_count == 0)
        self.post_valid_data_to_quote(response_code=400)
        self.assertTrue(self.mock_mailer.call_count == 0)


# This one fails
class ConfirmCommentTestCase(TestCase):
    def setUp(self):
        self.patcher = patch("django_comments_xtd.views.send_mail")
        self.mock_mailer = self.patcher.start()
        # Create random string so that it's harder for zlib to compress
        content = "".join(random.choice(string.printable) for _ in range(6096))
        self.article = Article.objects.create(
            title="September",
            slug="september",
            body="In September..." + content,
        )
        self.form = django_comments.get_form()(self.article)
        data = {
            "name": "Bob",
            "email": "bob@example.com",
            "followup": True,
            "reply_to": 0,
            "level": 1,
            "order": 1,
            "comment": "Es war einmal eine kleine...",
        }
        data.update(self.form.initial)
        self.assertTrue(self.mock_mailer.call_count == 0)
        post_article_comment(data, self.article)
        self.assertTrue(self.mock_mailer.call_count == 1)
        self.key = str(
            re.search(
                r"http://.+/confirm/(?P<key>\S+)/",
                self.mock_mailer.call_args[0][1],
            ).group("key")
        )

    def tearDown(self):
        self.patcher.stop()

    def test_confirm_url_is_short_enough(self):
        # Tests that the length of the confirm url's length isn't
        # dependent on the article length.
        length = len(reverse("comments-xtd-confirm", kwargs={"key": self.key}))
        self.assertLessEqual(length, 4096, "Urls can only be a max of 4096")

    def test_400_on_bad_signature(self):
        response = confirm_comment_url(self.key[:-1])
        self.assertEqual(response.status_code, 400)

    def test_consecutive_confirmation_url_visits_doesnt_fail(self):
        # test that consecutive visits to the same confirmation URL produce
        # a Http 404 code, as the comment has already been verified in the
        # first visit
        response = confirm_comment_url(self.key)
        self.assertEqual(response.status_code, 302)
        confirm_comment_url(self.key)
        self.assertEqual(response.status_code, 302)

    def test_signal_receiver_may_discard_the_comment(self):
        # test that receivers of signal confirmation_received may return False
        # and thus rendering a template_discarded output
        def on_signal(sender, comment, request, **kwargs):
            return False

        self.assertEqual(self.mock_mailer.call_count, 1)  # sent during setUp
        signals.confirmation_received.connect(on_signal)
        response = confirm_comment_url(self.key)
        # mailing avoided by on_signal:
        self.assertEqual(self.mock_mailer.call_count, 1)
        self.assertTrue(response.content.find(b"Comment discarded") > -1)

    def test_comment_is_created_and_view_redirect(self):
        # testing that visiting a correct confirmation URL creates a XtdComment
        # and redirects to the article detail page
        Site.objects.get_current().domain = "testserver"  # django bug #7743
        response = confirm_comment_url(self.key, follow=False)
        data = signed.loads(self.key, extra_key=settings.COMMENTS_XTD_SALT)
        comment = XtdComment.objects.get(
            content_type=data["content_type"],
            user_name=data["user_name"],
            user_email=data["user_email"],
            submit_date=data["submit_date"],
        )
        self.assertTrue(comment is not None)
        self.assertEqual(response.url, comment.get_absolute_url())

    def test_notify_comment_followers(self):
        # send a couple of comments to the article with followup=True and check
        # that when the second comment is confirmed a followup notification
        # email is sent to the user who sent the first comment
        self.assertEqual(self.mock_mailer.call_count, 1)
        confirm_comment_url(self.key)
        # no comment followers yet:
        self.assertEqual(self.mock_mailer.call_count, 1)
        # send 2nd comment
        self.form = django_comments.get_form()(self.article)
        data = {
            "name": "Alice",
            "email": "alice@example.com",
            "followup": True,
            "reply_to": 0,
            "level": 1,
            "order": 1,
            "comment": "Es war einmal eine kleine...",
        }
        data.update(self.form.initial)
        response = post_article_comment(data, article=self.article)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/comments/posted/?c="))
        self.assertEqual(self.mock_mailer.call_count, 2)
        self.key = re.search(
            r"http://.+/confirm/(?P<key>\S+)/", self.mock_mailer.call_args[0][1]
        ).group("key")
        confirm_comment_url(self.key)
        self.assertEqual(self.mock_mailer.call_count, 3)
        self.assertTrue(
            # We can find the 'comment' in the text_message.
            self.mock_mailer.call_args[0][1].index(data["comment"]) > -1
        )
        articles_title = f"Post: {self.article.title}"
        self.assertTrue(
            # We can find article's title (comment.content_object.title).
            self.mock_mailer.call_args[0][1].index(articles_title) > -1
        )
        self.assertTrue(self.mock_mailer.call_args[0][3] == ["bob@example.com"])
        self.assertTrue(
            self.mock_mailer.call_args[0][1].find(
                "There is a new comment following up yours."
            )
            > -1
        )

    def test_notify_followers_dupes(self):
        # first of all confirm Bob's comment otherwise it doesn't reach DB
        confirm_comment_url(self.key)
        # then put in play pull-request-15's assert...
        # https://github.com/danirus/django-comments-xtd/pull/15
        diary = Diary.objects.create(body="Lorem ipsum", allow_comments=True)
        self.assertEqual(diary.pk, self.article.pk)

        self.form = django_comments.get_form()(diary)
        data = {
            "name": "Charlie",
            "email": "charlie@example.com",
            "followup": True,
            "reply_to": 0,
            "level": 1,
            "order": 1,
            "comment": "Es war einmal eine kleine...",
        }
        data.update(self.form.initial)
        response = post_diary_comment(data, diary_entry=diary)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/comments/posted/?c="))
        self.key = str(
            re.search(
                r"http://.+/confirm/(?P<key>\S+)/",
                self.mock_mailer.call_args[0][1],
            ).group("key")
        )
        # 1) confirmation for Bob (sent in `setUp()`)
        # 2) confirmation for Charlie
        self.assertEqual(self.mock_mailer.call_count, 2)
        response = confirm_comment_url(self.key)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/comments/cr/"))
        self.assertEqual(self.mock_mailer.call_count, 2)

        self.form = django_comments.get_form()(self.article)
        data = {
            "name": "Alice",
            "email": "alice@example.com",
            "followup": True,
            "reply_to": 0,
            "level": 1,
            "order": 1,
            "comment": "Es war einmal eine kleine...",
        }
        data.update(self.form.initial)
        response = post_article_comment(data, article=self.article)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/comments/posted/?c="))
        self.assertEqual(self.mock_mailer.call_count, 3)
        self.key = re.search(
            r"http://.+/confirm/(?P<key>\S+)/", self.mock_mailer.call_args[0][1]
        ).group("key")
        confirm_comment_url(self.key)
        self.assertEqual(self.mock_mailer.call_count, 4)
        self.assertTrue(self.mock_mailer.call_args[0][3] == ["bob@example.com"])
        self.assertTrue(
            self.mock_mailer.call_args[0][1].find(
                "There is a new comment following up yours."
            )
            > -1
        )

    def test_no_notification_for_same_user_email(self):
        # test that a follow-up user_email don't get a notification when
        # sending another email to the thread
        self.assertEqual(self.mock_mailer.call_count, 1)
        confirm_comment_url(self.key)  # confirm Bob's comment
        # no comment followers yet:
        self.assertEqual(self.mock_mailer.call_count, 1)
        # send Bob's 2nd comment
        self.form = django_comments.get_form()(self.article)
        data = {
            "name": "Alice",
            "email": "bob@example.com",
            "followup": True,
            "reply_to": 0,
            "level": 1,
            "order": 1,
            "comment": "Bob's comment he shouldn't get notified about",
        }
        data.update(self.form.initial)
        post_article_comment(data, self.article)
        self.assertEqual(self.mock_mailer.call_count, 2)
        self.key = re.search(
            r"http://.+/confirm/(?P<key>\S+)/", self.mock_mailer.call_args[0][1]
        ).group("key")
        confirm_comment_url(self.key)
        self.assertEqual(self.mock_mailer.call_count, 2)


class ReplyNoCommentTestCase(TestCase):
    def test_reply_non_existing_comment_raises_404(self):
        response = self.client.get(
            reverse("comments-xtd-reply", kwargs={"cid": 1})
        )
        self.assertContains(response, "404", status_code=404)


class ReplyCommentToArticleTestCase(TestCase):
    def setUp(self):
        article = Article.objects.create(
            title="September",
            slug="september",
            body="What I did on September...",
        )
        article_ct = ContentType.objects.get(app_label="tests", model="article")
        site = Site.objects.get(pk=1)

        # post Comment 1 to article, level 0
        XtdComment.objects.create(
            content_type=article_ct,
            object_pk=article.id,
            content_object=article,
            site=site,
            comment="comment 1 to article",
            submit_date=datetime.now(),
        )

        # post Comment 2 to article, level 1
        XtdComment.objects.create(
            content_type=article_ct,
            object_pk=article.id,
            content_object=article,
            site=site,
            comment="comment 1 to comment 1",
            submit_date=datetime.now(),
            parent_id=1,
        )

        # post Comment 3 to article, level 2 (max according to test settings)
        XtdComment.objects.create(
            content_type=article_ct,
            object_pk=article.id,
            content_object=article,
            site=site,
            comment="comment 1 to comment 1",
            submit_date=datetime.now(),
            parent_id=2,
        )

    @patch.multiple(
        "django_comments_xtd.conf.settings", COMMENTS_XTD_MAX_THREAD_LEVEL=2
    )
    def test_not_allow_threaded_reply_raises_403(self):
        response = self.client.get(
            reverse("comments-xtd-reply", kwargs={"cid": 3})
        )
        self.assertEqual(response.status_code, 403)


class ReplyCommentToQuoteTestCase(TestCase):
    def setUp(self):
        quote = Quote.objects.create(
            title="September",
            slug="september",
            quote="No por mucho madrugar...",
        )
        quote_ct = ContentType.objects.get(app_label="tests", model="quote")
        site = Site.objects.get(pk=1)

        # post Comment 1 to quote, level 0
        XtdComment.objects.create(
            content_type=quote_ct,
            object_pk=quote.id,
            content_object=quote,
            site=site,
            comment="comment 1 to quote",
            submit_date=datetime.now(),
        )

    def test_reply_as_visitor_when_only_users_can_post(self):
        response = self.client.get(
            reverse("comments-xtd-reply", kwargs={"cid": 1})
        )
        self.assertEqual(response.status_code, 302)  # Redirect to login.
        self.assertTrue(response.url.startswith(settings.LOGIN_URL))


class MuteFollowUpTestCase(TestCase):
    def setUp(self):
        # Creates an article and send two comments to the article with
        # follow-up notifications. First comment doesn't have to send any
        #  notification.
        # Second comment has to send one notification (to Bob).
        self.patcher = patch("django_comments_xtd.views.send_mail")
        self.mock_mailer = self.patcher.start()
        self.article = Article.objects.create(
            title="September", slug="september", body="John's September"
        )
        self.form = django_comments.get_form()(self.article)

        # Bob sends 1st comment to the article with follow-up
        data = {
            "name": "Bob",
            "email": "bob@example.com",
            "followup": True,
            "reply_to": 0,
            "level": 1,
            "order": 1,
            "comment": "Nice September you had...",
        }
        data.update(self.form.initial)
        response = post_article_comment(data, self.article)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/comments/posted/?c="))
        self.assertTrue(self.mock_mailer.call_count == 1)
        bobkey = str(
            re.search(
                r"http://.+/confirm/(?P<key>\S+)/",
                self.mock_mailer.call_args[0][1],
            ).group("key")
        )
        confirm_comment_url(bobkey)  # confirm Bob's comment

        # Alice sends 2nd comment to the article with follow-up
        data = {
            "name": "Alice",
            "email": "alice@example.com",
            "followup": True,
            "reply_to": 1,
            "level": 1,
            "order": 1,
            "comment": "Yeah, great photos",
        }
        data.update(self.form.initial)
        response = post_article_comment(data, self.article)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/comments/posted/?c="))
        self.assertTrue(self.mock_mailer.call_count == 2)
        alicekey = str(
            re.search(
                r"http://.+/confirm/(?P<key>\S+)/",
                self.mock_mailer.call_args[0][1],
            ).group("key")
        )
        confirm_comment_url(alicekey)  # confirm Alice's comment

        # Bob receives a follow-up notification
        self.assertTrue(self.mock_mailer.call_count == 3)
        self.bobs_mutekey = str(
            re.search(
                r"http://.+/mute/(?P<key>\S+)/",
                self.mock_mailer.call_args[0][1],
            ).group("key")
        )

    def tearDown(self):
        self.patcher.stop()

    def get_mute_followup_url(self, key):
        request = request_factory.get(
            reverse("comments-xtd-mute", kwargs={"key": key}), follow=True
        )
        request.user = AnonymousUser()
        response = views.mute(request, key)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.find(b"Comment thread muted") > -1)
        return response

    def test_mute_followup_notifications(self):
        # Bob's receive a notification and click on the mute link to
        # avoid additional comment messages on the same article.
        expected_mailer_call_count = 4
        self.get_mute_followup_url(self.bobs_mutekey)
        # Alice sends 3rd comment to the article with follow-up
        data = {
            "name": "Alice",
            "email": "alice@example.com",
            "followup": True,
            "reply_to": 2,
            "level": 1,
            "order": 1,
            "comment": "And look at this and that...",
        }
        data.update(self.form.initial)
        response = post_article_comment(data, self.article)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/comments/posted/?c="))
        # Alice confirms her comment...
        self.assertTrue(
            self.mock_mailer.call_count == expected_mailer_call_count
        )
        alicekey = str(
            re.search(
                r"http://.+/confirm/(?P<key>\S+)/",
                self.mock_mailer.call_args[0][1],
            ).group("key")
        )
        confirm_comment_url(alicekey)  # confirm Alice's comment
        # Alice confirmed her comment, but this time Bob won't receive any
        # notification, neither do Alice being the sender
        self.assertTrue(
            self.mock_mailer.call_count == expected_mailer_call_count
        )


class HTMLDisabledMailTestCase(TestCase):
    def setUp(self):
        # Create an article and send a comment. Test method will check headers
        # to see whether messages have multiparts or not.
        self.patcher = patch("django_comments_xtd.views.send_mail")
        self.mock_mailer = self.patcher.start()
        self.article = Article.objects.create(
            title="September", slug="september", body="John's September"
        )
        self.form = django_comments.get_form()(self.article)

        # Bob sends 1st comment to the article with follow-up
        self.data = {
            "name": "Bob",
            "email": "bob@example.com",
            "followup": True,
            "reply_to": 0,
            "level": 1,
            "order": 1,
            "comment": "Nice September you had...",
        }
        self.data.update(self.form.initial)

    def tearDown(self):
        self.patcher.stop()

    @patch.multiple(
        "django_comments_xtd.conf.settings", COMMENTS_XTD_SEND_HTML_EMAIL=False
    )
    def test_mail_does_not_contain_html_part(self):
        with patch.multiple(
            "django_comments_xtd.conf.settings",
            COMMENTS_XTD_SEND_HTML_EMAIL=False,
        ):
            response = post_article_comment(self.data, self.article)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith("/comments/posted/?c="))
            self.assertTrue(self.mock_mailer.call_count == 1)
            self.assertTrue(self.mock_mailer.call_args[1]["html"] is None)

    def test_mail_does_contain_html_part(self):
        response = post_article_comment(self.data, self.article)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/comments/posted/?c="))
        self.assertTrue(self.mock_mailer.call_count == 1)
        self.assertTrue(self.mock_mailer.call_args[1]["html"] is not None)