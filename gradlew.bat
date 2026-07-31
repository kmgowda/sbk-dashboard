@if "%DEBUG%" == "" @echo off
@rem ##########################################################################
@rem
@rem  Gradle startup script for Windows
@rem
@rem ##########################################################################

@rem
@rem  Copyright (c) KMG. All Rights Reserved.
@rem
@rem  Licensed under the Apache License, Version 2.0 (the "License");
@rem  you may not use this file except in compliance with the License.
@rem
@rem      http://www.apache.org/licenses/LICENSE-2.0
@rem

@rem Set local scope for the variables with windows NT shell
if "%OS%"=="Windows_NT" setlocal

set DIRNAME=%~dp0
if "%DIRNAME%" == "" set DIRNAME=.
set APP_BASE_NAME=%~n0
set APP_HOME=%DIRNAME%

@rem Add default JVM options here. You can also use JAVA_OPTS and GRADLE_OPTS to pass JVM options to this script.
set DEFAULT_JVM_OPTS=

for /f "tokens=2 delims==" %%a in ('findstr /b "javaVersion=" "%APP_HOME%gradle.properties"') do set REQUIRED_JAVA_VERSION=%%a
for /f "tokens=2 delims==" %%a in ('findstr /b "distributionUrl=" "%APP_HOME%gradle\wrapper\gradle-wrapper.properties"') do set GRADLE_DISTRIBUTION_URL=%%a
for /f "tokens=2 delims=-" %%a in ("%GRADLE_DISTRIBUTION_URL%") do set GRADLE_VERSION=%%a

if not defined REQUIRED_JAVA_VERSION (
    echo ERROR: Unable to read the required Java version from gradle.properties.
    goto fail
)
if not defined GRADLE_VERSION (
    echo ERROR: Unable to read the Gradle version from gradle-wrapper.properties.
    goto fail
)

@rem Find java.exe
@rem Check SBK_JAVA_HOME first, then fall back to JAVA_HOME, then system java
if defined SBK_JAVA_HOME (
    set JAVA_SOURCE=SBK_JAVA_HOME
    goto findJavaFromSbkJavaHome
)

if defined JAVA_HOME (
    set JAVA_SOURCE=JAVA_HOME
    goto findJavaFromJavaHome
)

set JAVA_SOURCE=PATH
set JAVA_EXE=java.exe
%JAVA_EXE% -version >NUL 2>&1
if "%ERRORLEVEL%" == "0" goto init

echo.
echo ERROR: SBK_JAVA_HOME and JAVA_HOME are not set and no 'java' command could be found in your PATH.
echo.
echo Please set the SBK_JAVA_HOME or JAVA_HOME variable in your environment to match the
echo location of your Java installation.

goto fail

:findJavaFromSbkJavaHome
set SBK_JAVA_HOME=%SBK_JAVA_HOME:"=%
set JAVA_EXE=%SBK_JAVA_HOME%/bin/java.exe

if exist "%JAVA_EXE%" goto init

echo.
echo ERROR: SBK_JAVA_HOME is set to an invalid directory: %SBK_JAVA_HOME%
echo.
echo Please set the SBK_JAVA_HOME variable in your environment to match the
echo location of your Java installation.

goto fail

:findJavaFromJavaHome
set JAVA_HOME=%JAVA_HOME:"=%
set JAVA_EXE=%JAVA_HOME%/bin/java.exe

if exist "%JAVA_EXE%" goto init

echo.
echo ERROR: JAVA_HOME is set to an invalid directory: %JAVA_HOME%
echo.
echo Please set the JAVA_HOME variable in your environment to match the
echo location of your Java installation.

goto fail

:init
@rem Validate Java against the configured project requirement.
for /f "tokens=3" %%a in ('"%JAVA_EXE%" -version 2^>^&1 ^| findstr /i "version"') do (
    set JAVA_VERSION=%%a
)
@rem Remove quotes and extract major version
set JAVA_VERSION=%JAVA_VERSION:"=%
for /f "tokens=1,2 delims=." %%a in ("%JAVA_VERSION%") do (
    set MAJOR_VERSION=%%a
)

echo Java source: %JAVA_SOURCE%
echo Java version: %JAVA_VERSION%
echo Gradle version: %GRADLE_VERSION%

@rem Check against the configured project Java requirement.
if %MAJOR_VERSION% LSS %REQUIRED_JAVA_VERSION% (
    echo.
    echo ================================================================================
    echo ERROR: SBK requires JDK %REQUIRED_JAVA_VERSION%, but found JDK %MAJOR_VERSION%
    echo ================================================================================
    echo.
    echo Current Java version: %MAJOR_VERSION%
    echo Required Java version: %REQUIRED_JAVA_VERSION%
    echo.
    echo To fix this issue, please set one of the following environment variables:
    echo.
    if "%SBK_JAVA_HOME%"=="" (
        echo   Option 1: Set SBK_JAVA_HOME to point to your required JDK installation
        echo            set SBK_JAVA_HOME=C:\path\to\jdk-%REQUIRED_JAVA_VERSION%
        echo.
    )
    if "%JAVA_HOME%"=="" (
        echo   Option 2: Set JAVA_HOME to point to your required JDK installation
        echo            set JAVA_HOME=C:\path\to\jdk-%REQUIRED_JAVA_VERSION%
        echo.
    )
    echo   Option 3: Install JDK %REQUIRED_JAVA_VERSION% and set SBK_JAVA_HOME or JAVA_HOME
    echo.
    echo For more information, see: https://github.com/kmgowda/SBK
    echo ================================================================================
    echo.
    exit /b 1
)

@rem Get command-line arguments, handling Windows variants

if not "%OS%" == "Windows_NT" goto win9xME_args

:win9xME_args
@rem Slurp the command line arguments.
set CMD_LINE_ARGS=
set _SKIP=2

:win9xME_args_slurp
if "x%~1" == "x" goto execute

set CMD_LINE_ARGS=%*

:execute
@rem Setup the command line

set CLASSPATH=%APP_HOME%\gradle\wrapper\gradle-wrapper.jar

@rem Execute Gradle
"%JAVA_EXE%" %DEFAULT_JVM_OPTS% %JAVA_OPTS% %GRADLE_OPTS% "-Dorg.gradle.appname=%APP_BASE_NAME%" -classpath "%CLASSPATH%" org.gradle.wrapper.GradleWrapperMain %CMD_LINE_ARGS%

:end
@rem End local scope for the variables with windows NT shell
if "%ERRORLEVEL%"=="0" goto mainEnd

:fail
rem Set variable GRADLE_EXIT_CONSOLE if you need the _script' return code instead of
rem the _cmd.exe /c_ return code!
if  not "" == "%GRADLE_EXIT_CONSOLE%" exit 1
exit /b 1

:mainEnd
if "%OS%"=="Windows_NT" endlocal

:omega
