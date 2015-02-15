app.controller('CourseGradesController', ['$scope', '$routeParams', '$http', '$q', 'Courses', 'Grades',
    function($scope, $routeParams, $http, $q, Courses, Grades) {
  var course_section_id = $routeParams.course_section_id;
  var course;
  var grades;
  $scope.gridData = {};
  $scope.gridData.columns = [{
    title: 'Student',
    readOnly: true,
    data: 'name',
    width: 100}];
  $scope.gridData.rows = [];
  $scope.htSettings = {};
  $scope.htSettings.afterChange = function(changes, source) {
    if (source !== 'loadData') {
      angular.forEach(changes, function(change) { 
        row = $scope.gridData.rows[change[0]];
        prop = change[1];
        oldVal = change[2];
        newVal = change[3];
        if (prop.substring(0, 6) === 'grade_') {
          marking_period = prop.substring(6);
          student = row.id;
          data = {
            student: student,
            marking_period: marking_period,
            course_section: course_section_id,
            grade: newVal
          };
          $http({
            method: "POST",
            url: "/api/set_grade/",
            data: data
          }).success(function(data, status){
            console.log(data);
          });
        }
      });
    }
  };
  
  $q.all([
    Courses.one(course_section_id).get().then(function(data) {
      course = data;
      $scope.course_name = course.course.fullname;
      angular.forEach(course.marking_period, function(mp) {
        $scope.gridData.columns.push({
          title: mp.name,
          data: 'grade_' + mp.id,
          width: 100
        });
      });
      $scope.gridData.columns.push({
        title: 'Final',
        width: 100
      });
    }),
    Grades.getList({enrollment__course_section: course_section_id}).then(function(data){
      grades = data;
    }),
  ]).then(function(){
    angular.forEach(course.enrollments, function(enrollment) {
      enrollment.name = enrollment.first_name + " " + enrollment.last_name;
      angular.forEach(grades, function(grade) {
        if (grade.student_id === enrollment.id) {
          enrollment['grade_' + grade.marking_period] = grade.grade;
        }
      });
      $scope.gridData.rows.push(enrollment);
    });
  })
}]);

app.factory('Courses', ['Restangular', function(Restangular) {
  return Restangular.service('sections');
}]);

app.factory('Grades', ['Restangular', function(Restangular) {
  return Restangular.service('grades');
}]);
