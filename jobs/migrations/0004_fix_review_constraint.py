from django import VERSION as DJANGO_VERSION
from django.db import migrations, models
from django.db.models import F, Q


def _reviewer_not_reviewee_constraint():
    kwargs = {'name': 'reviewer_not_reviewee'}
    condition = ~Q(reviewer=F('reviewee'))
    if DJANGO_VERSION >= (6, 0):
        kwargs['condition'] = condition
    else:
        kwargs['check'] = condition
    return models.CheckConstraint(**kwargs)


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0003_certificate'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='review',
            name='reviewer_not_reviewee',
        ),
        migrations.AddConstraint(
            model_name='review',
            constraint=_reviewer_not_reviewee_constraint(),
        ),
    ]
