// Пример для flowgen: небольшая программа со всеми основными конструкциями.
// Читает числа из файлов data1.txt … data3.txt, считает статистику и пишет отчёт.
#include <iostream>
#include <fstream>
#include <string>

using namespace std;

const int N = 100;
const int FILES = 3;

// Читает не больше N чисел; возвращает, сколько прочитано.
int read_numbers(ifstream& in, double a[]) {
    int n = 0;
    double x;
    while (n < N && in >> x) {
        if (x != x) continue;        // NaN пропускаем
        a[n] = x;
        n++;
    }
    return n;
}

// Индекс максимального элемента.
int index_of_max(const double a[], int n) {
    int best = 0;
    for (int i = 1; i < n; i++) {
        if (a[i] > a[best])
            best = i;
    }
    return best;
}

// Оценка по среднему значению.
char grade(double avg) {
    if (avg >= 90) return 'A';
    else if (avg >= 75) return 'B';
    else if (avg >= 60) return 'C';
    return 'D';
}

void print_report(ofstream& out, const double a[], int n) {
    double sum = 0;
    for (int i = 0; i < n; i++)
        sum += a[i];
    double avg = sum / n;
    out << "Среднее: " << avg << endl;
    switch (grade(avg)) {
    case 'A':
        out << "Отлично" << endl;
        break;
    case 'B':
    case 'C':
        out << "Нормально" << endl;
        break;
    default:
        out << "Плохо" << endl;
    }
    out << "Максимум: " << a[index_of_max(a, n)] << endl;
}

int main() {
    ofstream out("report.txt");
    if (!out.is_open()) {
        cout << "Не удалось создать report.txt" << endl;
        return 1;
    }
    double a[N];
    for (int k = 1; k <= FILES; k++) {
        string name = "data" + to_string(k) + ".txt";
        ifstream in(name);
        if (!in.is_open()) {
            out << "Нет файла " << name << endl;
            continue;
        }
        int n = read_numbers(in, a);
        if (n == 0) {
            out << "Файл " << name << " пуст" << endl;
            continue;
        }
        out << "Файл " << name << ": " << n << " чисел" << endl;
        print_report(out, a, n);
    }
    int answer;
    do {
        cout << "Показать отчёт? (1 — да, 0 — нет): ";
        cin >> answer;
    } while (answer != 0 && answer != 1);
    if (answer == 1)
        cout << "Отчёт записан в report.txt" << endl;
    return 0;
}
