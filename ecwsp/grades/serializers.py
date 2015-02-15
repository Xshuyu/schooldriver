from rest_framework import serializers
from .models import Grade
from ecwsp.sis.models import Student
from ecwsp.schedule.models import (
    MarkingPeriod, CourseSection, CourseEnrollment)


class GradeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Grade
        fields = ('grade', 'marking_period', 'student_id', 'course_section_id',
                  'enrollment')


class SetGradeSerializer(serializers.Serializer):
    marking_period = serializers.PrimaryKeyRelatedField(
        queryset=MarkingPeriod.objects.all(), required=False)
    student = serializers.PrimaryKeyRelatedField(
        queryset=Student.objects.all(), required=False, default=None)
    course_section = serializers.PrimaryKeyRelatedField(
        queryset=CourseSection.objects.all(), required=False, default=None)
    enrollment = serializers.PrimaryKeyRelatedField(
        queryset=CourseEnrollment.objects.all(), required=False, default=None)
    grade = serializers.CharField(max_length=5, allow_blank=False)

    def validate(self, data):
        INVALID_STRING = (
            "Must set either enrollment, or student and course_sectoin")
        if data['enrollment'] is None:
            if data['student'] is None or data['course_section'] is None:
                raise serializers.ValidationError(INVALID_STRING)
        return data
