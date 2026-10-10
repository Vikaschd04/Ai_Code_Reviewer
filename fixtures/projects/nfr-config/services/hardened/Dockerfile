FROM eclipse-temurin:21.0.4_7-jre
COPY app.jar /app/app.jar
USER 10001
HEALTHCHECK --interval=30s CMD ["java", "-version"]
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
