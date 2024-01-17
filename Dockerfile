FROM python:3.10

RUN apt update && apt install -y g++ git && pip install --upgrade pip

RUN useradd modmail
USER modmail
WORKDIR /home/modmail

ENV PATH="/home/modmail/.local/bin:${PATH}"

COPY --chown=modmail:modmail . .
RUN pip install -r requirements.txt --user

ENV USING_DOCKER yes

CMD ["python","bot.py"]
