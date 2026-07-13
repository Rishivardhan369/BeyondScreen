from django.http import Http404, HttpResponse
from django.shortcuts import render

from .forms import PostcardForm
from .services import (
    format_screen_time,
    generate_postcard,
    render_postcard_pdf,
    render_postcard_png,
)


def home(request):
    if request.method == "POST":
        form = PostcardForm(request.POST, request.FILES)
        if form.is_valid():
            data = form.cleaned_data
            filename = data["file"].name if data["file"] else None
            postcard = generate_postcard(
                mood=data["mood"],
                goal=data["goal"],
                screen_time=format_screen_time(data.get("screen_time")),
                has_report=bool(filename),
            )
            postcard["filename"] = filename
            request.session["postcard"] = postcard
            return render(request, "result.html", postcard)
    else:
        form = PostcardForm()

    return render(request, "home.html", {"form": form})


def download_postcard(request, file_format):
    postcard = request.session.get("postcard")
    if not postcard:
        raise Http404("Generate a postcard before downloading it.")

    if file_format == "png":
        content = render_postcard_png(postcard)
        content_type = "image/png"
    elif file_format == "pdf":
        content = render_postcard_pdf(postcard)
        content_type = "application/pdf"
    else:
        raise Http404("Unsupported postcard format.")

    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="unscroll-postcard.{file_format}"'
    return response
