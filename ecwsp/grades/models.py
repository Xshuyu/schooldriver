from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, signals
from django.conf import settings
from django.core.validators import MaxLengthValidator
from ecwsp.sis.models import Student
from ecwsp.sis.helper_functions import round_as_decimal
from ecwsp.administration.models import Configuration
from django_cached_field import CachedDecimalField

import decimal
from decimal import Decimal
import datetime
import ecwsp

class GradeComment(models.Model):
    id = models.IntegerField(primary_key=True)
    comment = models.CharField(max_length=500)

    def __unicode__(self):
        return unicode(self.id) + ": " + unicode(self.comment)

    class Meta:
        ordering = ('id',)


def grade_comment_length_validator(value):
    max_length = int(Configuration.get_or_default('Grade comment length limit').value)
    validator = MaxLengthValidator(max_length)
    return validator(value)

class StudentMarkingPeriodGrade(models.Model):
    """ Stores marking period grades for students, only used for cache """
    student = models.ForeignKey('sis.Student')
    marking_period = models.ForeignKey('schedule.MarkingPeriod', blank=True, null=True)
    grade = CachedDecimalField(max_digits=5, decimal_places=2, blank=True, null=True, verbose_name="MP Average")

    class Meta:
        unique_together = ('student', 'marking_period')
        
    def get_scaled_average(self, rounding=2):
        """ Convert to scaled grade first, then average
        Burke Software does not endorse this as a precise way to calculate averages """
        grade_total = 0.0
        course_count = 0
        for grade in self.student.grade_set.filter(marking_period=self.marking_period, grade__isnull=False):
            grade_value = grade.optimized_grade_to_scale(letter=False)
            grade_total += float(grade_value)
            course_count += 1
        average = grade_total / course_count
        return round_as_decimal(average, rounding)

    @staticmethod
    def build_all_cache():
        """ Create object for each student * possible marking periods """
        for student in Student.objects.all():
            marking_periods = student.courseenrollment_set.values('course_section__marking_period').annotate(Count('course_section__marking_period'))
            for marking_period in marking_periods:
                StudentMarkingPeriodGrade.objects.get_or_create(
                    student=student, marking_period_id=marking_period['course_section__marking_period'])

    def calculate_grade(self):
        return self.student.grade_set.filter(
            course_section__courseenrollment__user=self.student, # make sure the student is still enrolled in the course!
            # each course's weight in the MP average is the course's number of
            # credits DIVIDED BY the count of marking periods for the course
            grade__isnull=False, override_final=False, marking_period=self.marking_period).extra(select={
            'ave_grade': '''
                Sum(grade *
                      (SELECT credits
                       FROM schedule_course
                       WHERE schedule_course.id = grades_grade.course_section_id) /
                      (SELECT Count(schedule_coursesection_marking_period.markingperiod_id)
                       FROM schedule_coursesection_marking_period
                       WHERE schedule_coursesection_marking_period.coursesection_id = grades_grade.course_section_id)) /
                Sum(
                      (SELECT credits
                       FROM schedule_course
                       WHERE schedule_course.id = grades_grade.course_section_id) /
                      (SELECT Count(schedule_coursesection_marking_period.markingperiod_id)
                       FROM schedule_coursesection_marking_period
                       WHERE schedule_coursesection_marking_period.coursesection_id = grades_grade.course_section_id))
            '''
        }).values('ave_grade')[0]['ave_grade']


class StudentYearGrade(models.Model):
    """ Stores the grade for an entire year, only used for cache """
    student = models.ForeignKey('sis.Student')
    year = models.ForeignKey('sis.SchoolYear')
    grade = CachedDecimalField(max_digits=5, decimal_places=2, blank=True, null=True, verbose_name="Year average")
    credits = CachedDecimalField(max_digits=5, decimal_places=2, blank=True, null=True)

    class Meta:
        unique_together = ('student', 'year')

    @staticmethod
    def build_cache_student(student):
        years = student.courseenrollment_set.values(
            'course_section__marking_period__school_year').annotate(Count('course_section__marking_period__school_year'))
        for year in years:
            if year['course_section__marking_period__school_year']:
                year_grade = StudentYearGrade.objects.get_or_create(
                    student=student,
                    year_id=year['course_section__marking_period__school_year']
                )[0]
                if year_grade.credits_recalculation_needed:
                    year_grade.recalculate_credits()
                if year_grade.grade_recalculation_needed:
                    year_grade.recalculate_grade()

    @staticmethod
    def build_all_cache(*args, **kwargs):
        """ Create object for each student * possible years """
        if 'instance' in kwargs:
            StudentYearGrade.build_cache_student(kwargs['instance'])
        else:
            for student in Student.objects.all():
                StudentYearGrade.build_cache_student(student)

    def calculate_credits(self):
        """ The number of credits a student has earned in 1 year """
        return self.calculate_grade_and_credits()[1]

    def calculate_grade_and_credits(self, date_report=None):
        """ Just recalculate them both at once
        returns (grade, credits) """
        total = Decimal(0)
        credits = Decimal(0)
        for course_enrollment in self.student.courseenrollment_set.filter(
            course_section__marking_period__show_reports=True,
            course_section__marking_period__school_year=self.year,
            course_section__course__credits__isnull=False,
            ).distinct():
            grade = course_enrollment.calculate_grade_real(date_report=date_report, ignore_letter=True)
            #print ('{}\t' * 3).format(course_enrollment.course, course_enrollment.course.credits, grade)
            if grade:
                total += grade * course_enrollment.course_section.course.credits
                credits += course_enrollment.course_section.course.credits
        if credits > 0:
            grade = total / credits
        else:
            grade = None
        if date_report == None: # If set would indicate this is not for cache!
            self.grade = grade
            self.credits = credits
            self.grade_recalculation_needed = False
            self.credits_recalculation_needed = False
            self.save()
        return (grade, credits)

    def calculate_grade(self, date_report=None):
        """ Calculate grade considering MP weights and course credits
        course_enrollment.calculate_real_grade returns a MP weighted result,
        so just have to consider credits
        """
        return self.calculate_grade_and_credits(date_report=date_report)[0]

    def get_grade(self, date_report=None, rounding=2):
        if date_report is None or date_report >= datetime.date.today():
            # Cache will always have the latest grade, so it's fine for
            # today's date and any future date
            return self.grade
        grade = self.calculate_grade(date_report=date_report)
        if rounding:
            grade = round_as_decimal(grade, rounding)
        return grade

signals.post_save.connect(StudentYearGrade.build_all_cache, sender=Student)


class GradeScale(models.Model):
    """ Translate a numeric grade to some other scale.
    Example: Letter grade or 4.0 scale. """
    name = models.CharField(max_length=255, unique=True)

    def __unicode__(self):
        return '{}'.format(self.name)

    def get_rule(self, grade):
        return self.gradescalerule_set.filter(min_grade__lte=grade, max_grade__gte=grade).first()

    def to_letter(self, grade):
        rule = self.get_rule(grade)
        if rule:
            return rule.letter_grade

    def to_numeric(self, grade):
        rule = self.get_rule(grade)
        if rule:
            return rule.numeric_scale


class GradeScaleRule(models.Model):
    """ One rule for a grade scale.  """
    min_grade = models.DecimalField(max_digits=5, decimal_places=2)
    max_grade = models.DecimalField(max_digits=5, decimal_places=2)
    letter_grade = models.CharField(max_length=50, blank=True)
    numeric_scale = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    grade_scale = models.ForeignKey(GradeScale)

    class Meta:
        unique_together = ('min_grade', 'max_grade', 'grade_scale')

    def __unicode__(self):
        return '{}-{} {} {}'.format(self.min_grade, self.max_grade, self.letter_grade, self.numeric_scale)


letter_grade_choices = (
        ("I", "Incomplete"),
        ("P", "Pass"),
        ("F", "Fail"),
        ("A", "A"),
        ("B", "B"),
        ("C", "C"),
        ("D", "D"),
        ("HP", "High Pass"),
        ("LP", "Low Pass"),
        ("M", "Missing"),
    )
class Grade(models.Model):
    student = models.ForeignKey('sis.Student')
    course_section = models.ForeignKey('schedule.CourseSection')
    marking_period = models.ForeignKey('schedule.MarkingPeriod', blank=True, null=True)
    date = models.DateField(auto_now=True, validators=settings.DATE_VALIDATORS)
    grade = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    override_final = models.BooleanField(default=False, help_text="Override final grade for marking period instead of calculating it.")
    comment = models.CharField(max_length=500, blank=True, validators=[grade_comment_length_validator])
    letter_grade = models.CharField(max_length=2, blank=True, null=True, help_text="Will override grade.", choices=letter_grade_choices)
    letter_grade_behavior = {
        # Letter grade: (*normalized* value for calculations, dominate any average)
        "I": (None, True),
        "P": (1, False),
        "F": (0, False),
        # Should A be 90 or 100? A-D aren't used in calculations yet, so just omit them.
        "HP": (1, False),
        "LP": (1, False),
        "M": (0, False),
    }
    letter_grade_choices = letter_grade_choices

    class Meta:
        unique_together = (("student", "course_section", "marking_period"),)
        permissions = (
            ("change_own_grade", "Change grades for own class"),
            ('change_own_final_grade','Change final YTD grades for own class'),
        )

    def display_grade(self):
        """ Returns full spelled out grade such as Fail, Pass, 60.05, B"""
        return self.get_grade(display=True)

    def set_grade(self, grade):
        """ set grade to decimal or letter
            if grade is less than 1 assume it's a percentage
            returns success (True or False)"""
        try:
            grade = Decimal(str(grade))
            if grade < 1:
                # assume grade is a percentage
                grade = grade * 100
            self.grade = grade
            self.letter_grade = None
            return True
        except decimal.InvalidOperation:
            grade = unicode.upper(unicode(grade)).strip()
            if grade in dict(self.letter_grade_choices):
                self.letter_grade = grade
                self.grade = None
                return True
            elif grade in ('', 'NONE'):
                self.grade = None
                self.letter_grade = None
                return True
            return False

    @staticmethod
    def validate_grade(grade):
        """ Determine if grade is valid or not """
        try:
            grade = Decimal(str(grade))
            if grade >= 0:
                return
            raise ValidationError('Grade must be above 0')
        except decimal.InvalidOperation:
            grade = unicode.upper(unicode(grade)).strip()
            if (grade in dict(letter_grade_choices) or
               grade in ('', 'NONE')):
                return
        raise ValidationError('Invalid letter grade.')

    def invalidate_cache(self):
        """ Invalidate any related caches """
        try:
            enrollment = self.course_section.courseenrollment_set.get(user=self.student)
            enrollment.flag_grade_as_stale()
            enrollment.flag_numeric_grade_as_stale()
        except ecwsp.schedule.models.CourseEnrollment.DoesNotExist:
            pass
        self.student.cache_gpa = self.student.calculate_gpa()
        if self.student.cache_gpa != "N/A":
            self.student.save()

    def optimized_grade_to_scale(self, letter):
        """ Optimized version of GradeScale.to_letter
        letter - True for letter grade, false for numeric (ex: 4.0 scale) """
        rule = GradeScaleRule.objects.filter(
                grade_scale__schoolyear__markingperiod=self.marking_period_id,
                min_grade__lte=self.grade, 
                max_grade__gte=self.grade,
                ).first()
        if letter:
            return rule.letter_grade
        return rule.numeric_scale

    def get_grade(self, letter=False, display=False, rounding=None,
        minimum=None, number=False):
        """
        letter: Converts to a letter based on GradeScale 
        display: For letter grade - Return display name instead of abbreviation.
        rounding: Numeric - round to this many decimal places.
        minimum: Numeric - Minimum allowed grade. Will not return lower than this.
        number: Consider stored numeric grade only
        Returns grade such as 90.03, P, or F
        """
        if self.letter_grade and not number:
            if display:
                return self.get_letter_grade_display()
            else:
                return self.letter_grade
        elif self.grade is not None:
            grade = self.grade
            if minimum:
                if grade < minimum:
                    grade = minimum
            if rounding != None:
                string = '%.' + str(rounding) + 'f'
                grade = string % float(str(grade))
            if letter == True:
                try:
                    return self.optimized_grade_to_scale(letter=True)
                except GradeScaleRule.DoesNotExist:
                    return "No Grade Scale"
            return grade
        else:
            return ""

    api_grade = property(get_grade, set_grade)

    def clean(self):
        ''' We must allow simulataneous letter and number grades. Grading mechanisms
        submit both; the number is used for calculations and the letter appears on
        reports. '''
        if self.marking_period_id == None:
            if Grade.objects.filter(
                    student=self.student,
                    course_section=self.course_section,
                    marking_period=None
                    ).exclude(id=self.id).exists():
                raise ValidationError('Student, Course Section, MarkingPeriod must be unique')

    def save(self, *args, **kwargs):
        super(Grade, self).save(*args, **kwargs)
        self.invalidate_cache()

    def delete(self, *args, **kwargs):
        super(Grade, self).delete(*args, **kwargs)
        self.invalidate_cache()

    def __unicode__(self):
        return unicode(self.get_grade(self))


    @staticmethod
    def populate_grade(student, marking_period, course_section):
        """
        make sure that each combination of Student/MarkingPeriod/CourseSection
        has a grade entity associated with it. If none exists, create one and
        set the course grade to "None". This method should be called on
        enrolling students to an exsiting course or creating a new course,
        or creating a new marking period, or creating a new cource section
        """
        grade_instance = Grade.objects.filter(
            student = student,
            course_section = course_section,
            marking_period = marking_period
        )
        if not grade_instance:
            new_grade = Grade(
                student = student,
                course_section = course_section,
                marking_period = marking_period,
                grade = None,
            )
            new_grade.save()

