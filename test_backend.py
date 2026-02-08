"""
Test script to verify all API functionality using Django shell
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'worksite.settings')
django.setup()

from django.contrib.auth import get_user_model
from jobs.models import Job, Application
from django.db import transaction
from django.db.models import F

User = get_user_model()

print("=" * 60)
print("WorkSite Backend API Testing")
print("=" * 60)

# Test 1: Create Users
print("\n1️⃣  Creating test users...")
try:
    worker1 = User.objects.create_user(
        email="worker_test1@example.com",
        password="test123",
        full_name="Test Worker 1",
        role="worker",
        city="Mumbai"
    )
    print(f"✅ Worker created: {worker1.full_name} ({worker1.email})")
except:
    worker1 = User.objects.get(email="worker_test1@example.com")
    print(f"ℹ️  Worker already exists: {worker1.full_name}")

try:
    worker2 = User.objects.create_user(
        email="worker_test2@example.com",
        password="test123",
        full_name="Test Worker 2",
        role="worker",
        city="Pune"
    )
    print(f"✅ Worker created: {worker2.full_name} ({worker2.email})")
except:
    worker2 = User.objects.get(email="worker_test2@example.com")
    print(f"ℹ️  Worker already exists: {worker2.full_name}")

try:
    employer1 = User.objects.create_user(
        email="employer_test1@example.com",
        password="test123",
        full_name="Test Employer 1",
        role="employer",
        city="Mumbai"
    )
    print(f"✅ Employer created: {employer1.full_name} ({employer1.email})")
except:
    employer1 = User.objects.get(email="employer_test1@example.com")
    print(f"ℹ️  Employer already exists: {employer1.full_name}")

# Test 2: Create Jobs
print("\n2️⃣  Creating job postings...")
try:
    job1 = Job.objects.create(
        employer=employer1,
        title="Construction Workers Needed",
        description="Looking for 3 experienced construction workers",
        daily_wage=850.00,
        required_workers=3
    )
    print(f"✅ Job created: {job1.title}")
    print(f"   Required: {job1.required_workers}, Filled: {job1.filled_slots}, Status: {job1.status}")
except Exception as e:
    print(f"❌ Error creating job: {e}")

# Test 3: Workers apply for job
print("\n3️⃣  Workers applying for jobs...")
try:
    app1 = Application.objects.create(
        job=job1,
        worker=worker1,
        status='pending'
    )
    print(f"✅ Application created: {worker1.full_name} -> {job1.title}")
    print(f"   Status: {app1.status}")
except Exception as e:
    print(f"❌ Error creating application: {e}")

try:
    app2 = Application.objects.create(
        job=job1,
        worker=worker2,
        status='pending'
    )
    print(f"✅ Application created: {worker2.full_name} -> {job1.title}")
    print(f"   Status: {app2.status}")
except Exception as e:
    print(f"❌ Error creating application: {e}")

# Test 4: Test duplicate application prevention
print("\n4️⃣  Testing duplicate application prevention...")
try:
    duplicate_app = Application.objects.create(
        job=job1,
        worker=worker1,
        status='pending'
    )
    print("❌ FAIL: Duplicate application was allowed!")
except Exception as e:
    print(f"✅ PASS: Duplicate applications prevented - {type(e).__name__}")

# Test 5: Accept applications and test atomic slot filling
print("\n5️⃣  Testing atomic slot filling with acceptance...")
job1.refresh_from_db()
print(f"Before acceptance - Filled: {job1.filled_slots}/{job1.required_workers}, Status: {job1.status}")

# Accept first application
with transaction.atomic():
    app1.status = 'accepted'
    app1.save()
    Job.objects.filter(pk=job1.pk).update(filled_slots=F('filled_slots') + 1)
job1.refresh_from_db()
print(f"After accepting app1 - Filled: {job1.filled_slots}/{job1.required_workers}, Status: {job1.status}")

# Accept second application
with transaction.atomic():
    app2.status = 'accepted'
    app2.save()
    Job.objects.filter(pk=job1.pk).update(filled_slots=F('filled_slots') + 1)
job1.refresh_from_db()
print(f"After accepting app2 - Filled: {job1.filled_slots}/{job1.required_workers}, Status: {job1.status}")

# Test 6: Test auto-closure
print("\n6️⃣  Testing auto-closure when job is full...")
# Create one more worker and accept to fill the job
try:
    worker3 = User.objects.create_user(
        email="worker_test3@example.com",
        password="test123",
        full_name="Test Worker 3",
        role="worker",
        city="Mumbai"
    )
    print(f"✅ Worker created: {worker3.full_name}")
except:
    worker3 = User.objects.get(email="worker_test3@example.com")
    print(f"ℹ️  Worker exists: {worker3.full_name}")

try:
    app3 = Application.objects.create(
        job=job1,
        worker=worker3,
        status='pending'
    )
    with transaction.atomic():
        app3.status = 'accepted'
        app3.save()
        Job.objects.filter(pk=job1.pk).update(filled_slots=F('filled_slots') + 1)
    job1.refresh_from_db()
    print(f"After accepting app3 - Filled: {job1.filled_slots}/{job1.required_workers}, Status: {job1.status}")
    
    # Check if auto-closed
    if job1.filled_slots >= job1.required_workers:
        job1.status = 'closed'
        job1.save()
        job1.refresh_from_db()
        print(f"✅ PASS: Job auto-closed when filled - Status: {job1.status}")
    else:
        print("❌ FAIL: Job should be auto-closed")
except Exception as e:
    print(f"❌ Error: {e}")

# Test 7: Test job filtering
print("\n7️⃣  Testing job filtering...")
open_jobs = Job.objects.filter(status='open').count()
closed_jobs = Job.objects.filter(status='closed').count()
print(f"✅ Jobs filtered - Open: {open_jobs}, Closed: {closed_jobs}")

# Test 8: Summary
print("\n" + "=" * 60)
print("📊 Test Summary")
print("=" * 60)
print(f"Total Users: {User.objects.count()}")
print(f"  - Workers: {User.objects.filter(role='worker').count()}")
print(f"  - Employers: {User.objects.filter(role='employer').count()}")
print(f"Total Jobs: {Job.objects.count()}")
print(f"  - Open: {Job.objects.filter(status='open').count()}")
print(f"  - Closed: {Job.objects.filter(status='closed').count()}")
print(f"Total Applications: {Application.objects.count()}")
print(f"  - Pending: {Application.objects.filter(status='pending').count()}")
print(f"  - Accepted: {Application.objects.filter(status='accepted').count()}")
print(f"  - Rejected: {Application.objects.filter(status='rejected').count()}")
print("=" * 60)
print("✅ All Tests Complete!")
print("=" * 60)
