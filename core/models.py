from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    bio = models.TextField(max_length=500, blank=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    newsletter_subscribe = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.user.username


class Postcard(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='postcards')
    mood = models.CharField(max_length=20)
    goal = models.CharField(max_length=20)
    screen_time = models.CharField(max_length=20, blank=True, null=True)
    has_report = models.BooleanField(default=False)
    filename = models.CharField(max_length=255, blank=True, null=True)
    haiku = models.TextField()
    reflection = models.TextField()
    action = models.TextField()
    pledge = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username}'s postcard from {self.created_at.strftime('%Y-%m-%d')}"


class DigitalSummary(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='digital_summaries')
    created_at = models.DateTimeField(auto_now_add=True)
    screen_time_minutes = models.IntegerField()
    wellness_score = models.IntegerField()
    category = models.CharField(max_length=20)
    insight = models.TextField()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} summary on {self.created_at.strftime('%Y-%m-%d %H:%M')}"


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    instance.userprofile.save()