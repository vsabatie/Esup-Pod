from django.shortcuts import render, redirect, get_object_or_404
from pod.main.views import in_maintenance
from django.urls import reverse
from django.contrib.sites.shortcuts import get_current_site
from django.contrib.auth.decorators import login_required
from django.utils.translation import ugettext as _
from django.views.decorators.csrf import csrf_protect
from django.contrib import messages

from pod.video.models import Video
from .forms import DressingForm
from .models import Dressing


@csrf_protect
@login_required(redirect_field_name="referrer")
def video_dressing(request, slug):
    """View for video dressing"""
    if in_maintenance():
        return redirect(reverse("maintenance"))
    video = get_object_or_404(Video, slug=slug, sites=get_current_site(request))

    return render(
        request,
        "video_dressing.html",
        {"video": video}
    )


@csrf_protect
@login_required(redirect_field_name="referrer")
def dressing_edit(request, dressing_id):
    dressing_edit = get_object_or_404(Dressing, id=dressing_id)
    form_dressing = DressingForm(
        instance=dressing_edit,
        is_staff=request.user.is_staff,
        is_superuser=request.user.is_superuser,
    )
    return render(
        request,
        'dressing_edit.html',
        {'dressing_edit': dressing_edit, "form": form_dressing})


def my_dressings(request):
    """Render the logged user's dressing"""
    if in_maintenance():
        return redirect(reverse("maintenance"))

    dressings = Dressing.objects.all()

    return render(request, "my_dressings.html",
                  {'dressings': dressings})
