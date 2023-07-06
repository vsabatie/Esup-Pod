from django.shortcuts import render, redirect, get_object_or_404
from pod.main.views import in_maintenance
from django.urls import reverse
from django.contrib.sites.shortcuts import get_current_site
from django.contrib.auth.decorators import login_required
from django.utils.translation import ugettext as _
from django.views.decorators.csrf import csrf_protect
from django.contrib import messages
from django.db.models import Q

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

    if request.method == 'POST':
        form_dressing = DressingForm(request.POST, instance=dressing_edit)
        if form_dressing.is_valid():
            messages.add_message(request, messages.INFO, _("Valide"))
            form_dressing.save()
            return redirect(reverse("dressing:my_dressings"))

    return render(
        request,
        'dressing_edit.html',
        {'dressing_edit': dressing_edit, "form": form_dressing})


@login_required(redirect_field_name="referrer")
def dressing_create(request):
    if request.method == 'POST':
        form_dressing = DressingForm(request.POST)
        if form_dressing.is_valid():
            form_dressing.save()
            return redirect('dressing:my_dressings')
    else:
        form_dressing = DressingForm()

    return render(
        request,
        'dressing_edit.html',
        {'dressing_create': dressing_create, "form": form_dressing})


@login_required(redirect_field_name="referrer")
def dressing_delete(request, dressing_id):
    dressing = get_object_or_404(Dressing, id=dressing_id)

    if request.method == 'POST':
        dressing.delete()
        return redirect('dressing:my_dressings')

    return render(request, 'dressing_confirm_delete.html',
                  {'dressing': dressing})


@csrf_protect
@login_required(redirect_field_name="referrer")
def my_dressings(request):
    """Render the logged user's dressing"""
    if in_maintenance():
        return redirect(reverse("maintenance"))
    user = request.user
    users_groups = user.owner.accessgroup_set.all()
    dressings = Dressing.objects.filter(
        Q(owners=user) |
        Q(users=user) |
        Q(allow_to_groups__in=users_groups)).distinct()

    return render(request, "my_dressings.html",
                  {'dressings': dressings})
