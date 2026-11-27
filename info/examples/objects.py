class Animal:
    def speak(self):
        return self.sound()

    def sound(self):
        return "..."

class Dog(Animal):
    def sound(self):
        return "woof"

class Puppy(Dog):
    pass

Puppy().speak()
