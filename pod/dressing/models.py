from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator

from django.contrib.auth.models import User
from pod.authentication.models import AccessGroup
from pod.podfile.models import CustomImageModel
from django.utils.translation import ugettext_lazy as _
from django.utils.translation import get_language
from pod.video.models import Video


class Dressing(models.Model):
    """Class describing Dressing objects."""
    TOP_RIGHT = 'top_right'
    TOP_LEFT = 'top_left'
    BOTTOM_RIGHT = 'bottom_right'
    BOTTOM_LEFT = 'bottom_left'
    POSITIONS = (
       (TOP_RIGHT, _('Top right')),
       (TOP_LEFT, _('Top left')),
       (BOTTOM_RIGHT, _('Bottom right')),
       (BOTTOM_LEFT, _('Bottom left')),
    )

    title = models.CharField(
        _("Title"),
        max_length=100,
        unique=True,
        help_text=_(
            "Please choose a title as short and accurate as "
            "possible, reflecting the main subject / context "
            "of the content.(max length: 100 characters)"
        ),
    )

    owners = models.ManyToManyField(
        User,
        related_name="owners_dressing",
        verbose_name=_("Owners"),
        blank=True,
    )

    users = models.ManyToManyField(
        User,
        related_name="users_dressing",
        verbose_name=_("Users"),
        blank=True,
    )

    allow_to_groups = models.ManyToManyField(
        AccessGroup,
        blank=True,
        verbose_name=_("Groups"),
        help_text=_("Select one or more groups who can upload video to this channel."),
    )

    watermark = models.ForeignKey(
        CustomImageModel,
        models.SET_NULL,
        blank=True,
        null=True,
        verbose_name=_("watermark"),
    )

    position = models.CharField(
        verbose_name=_("position"),
        max_length=200,
        choices=POSITIONS,
        default=TOP_RIGHT,
        blank=True,
        null=True,
    )

    opacity = models.PositiveIntegerField(
        default=100,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        blank=True,
        null=True,
        verbose_name=_("opacity"),
    )

    opening_credits = models.ForeignKey(
        Video,
        related_name='%(class)s_opening_credits',
        verbose_name=_("Opening credits"),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    ending_credits = models.ForeignKey(
        Video,
        related_name='%(class)s_ending_credits',
        verbose_name=_("Ending credits"),
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = _("Video dressing")
        verbose_name_plural = _("Video dressings")
