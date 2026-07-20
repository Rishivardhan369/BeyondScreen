import sys
sys.path.insert(0, '.')
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'unscroll.settings')
import django
django.setup()

from django.template import Context, Template
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpRequest

# Create a request and add a message
request = HttpRequest()
storage = FallbackStorage(request)
storage.add(message='Test message', level_tags='success')
messages = list(get_messages(request))

# Template that extends base.html
template_str = '''
{% extends \"base.html\" %}
{% block content %}
<h1>Test</h1>
{% endblock %}
'''
template = Template(template_str)
context = Context({'request': request, 'messages': messages})
rendered = template.render(context)
print('SUCCESS' if 'toast-container' in rendered else 'FAILED: missing toast-container')
print('SUCCESS' if 'toast-success' in rendered else 'FAILED: missing toast-success')
print('SUCCESS' if 'Test message' in rendered else 'FAILED: missing message text')
